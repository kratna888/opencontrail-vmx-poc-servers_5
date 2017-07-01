import os
import sys
import json
import re
from oslo_config import cfg
from jinja2 import Environment, FileSystemLoader
from netaddr import IPNetwork
from time import sleep, strftime

from utils.helpers import execute, from_project_root, get_project_root
from utils.remoteoperations import RemoteConnection

CONF = cfg.CONF

sk_img_path = '/var/www/html/pockit_images'
internet_ip = '8.8.8.8'
internet_www = 'www.google.com'
sk_libvirt_img = 'libvirt-1.2.19.tar.gz'

def get_ip(ip_w_pfx):
    return str(IPNetwork(ip_w_pfx).ip)

def get_netmask(ip_w_pfx):
        return str(IPNetwork(ip_w_pfx).netmask)

def waiting(count):
    for i in range(count):
        print ".",
        sys.stdout.flush()
        sleep(1)
    print "\n"


class vmxDeploy(object):

    def __init__(self):
        global jinja_env
        jinja_env = Environment(loader=FileSystemLoader(from_project_root('vmx')))

        svr1name = CONF['DEFAULTS']['bms'][0]
        self.svr2_bms_name = CONF['DEFAULTS']['bms'][1]

        self.server1_ip = get_ip(CONF[svr1name]['management_address'])
        server2_ip = get_ip(CONF[self.svr2_bms_name]['management_address'])
        server2_user = CONF['DEFAULTS']['root_username']
        server2_passwd = CONF['DEFAULTS']['root_password']

        self.svr2_bms = RemoteConnection()
        self.svr2_bms.connect(server2_ip, username=server2_user, password=server2_passwd)
        cmd = 'eval echo ~$USER'
        self.svr2_bms_home = self.svr2_bms.execute_cmd(cmd, timeout=10)

    def copy_vmx_images(self):
        print "Copying vMX image onto %s"%self.svr2_bms_name

        vmximage = CONF['DEFAULTS']['vmximage']
        cmd = 'rm -rf images/'
        self.svr2_bms.execute_cmd(cmd, timeout=10)
        cmd = 'mkdir -p images'
        self.svr2_bms.execute_cmd(cmd, timeout=10)

        cmd = '{0}/images/'.format(self.svr2_bms_home)
        self.svr2_bms.chdir(cmd)
        cmd = 'wget -q http://{0}/pockit_images/{1} -O {2}/images/{1}'.format(self.server1_ip,vmximage, self.svr2_bms_home)
        if self.svr2_bms.status_command(cmd) != 0:
            print "\n"
            print "Command: %s"%(cmd)
            print "Response: %s"%(res)
            print "=================================================="
            print "Error in downloading vMX image on to %s"%(self.svr2_bms_name)
            print "=================================================="
            sys.exit(1)
        else:
            print "Downloaded vMX image on to %s successfully"%(self.svr2_bms_name)


    def configure_server_uplink_intf(self):

        print "Configuring external/internet facing interface and routes"

        vm = 'vmx'
        intf = CONF[vm]['management_interface']
        server_ext_ip = get_ip(CONF[vm]['management_address'])
        server_ext_mask = get_netmask(CONF[vm]['management_address'])
        gateway = CONF['DEFAULTS']['vmx_ext_gateway']
        dns_server = CONF[vm]['dns_servers']
        dns_search = CONF[vm]['dns_search']

        cmd = '/sbin/ifconfig {0} {1} netmask {2} up'.format(intf, server_ext_ip, server_ext_mask)
        self.svr2_bms.execute_cmd(cmd, timeout=10)
        cmd = 'route delete default'
        self.svr2_bms.execute_cmd(cmd, timeout=10)
        cmd = 'route add default gw {}'.format(gateway)
        self.svr2_bms.execute_cmd(cmd, timeout=10)

        #update /etc/network/interfaces file with this ip
        cmd = 'cat /etc/network/interfaces'
        res = self.svr2_bms.execute_cmd(cmd, timeout=10)
        if re.search(r'%s'%intf, res, re.M|re.I):
            print "external interface configuration exists."
        else:
            print "external interface configuration does not exists, hence configuring"
            cmd = '(echo ;echo auto {}; echo iface {} inet static; echo address {}; echo netmask {}; echo gateway {}; echo dns-nameservers {}; echo dns-search {}) >> /etc/network/interfaces'.format(intf, intf, server_ext_ip, server_ext_mask, gateway, dns_server, dns_search)
            self.svr2_bms.execute_cmd(cmd, timeout=30)
            #add nameserver in /etc/resolv.conf
            cmd = '(echo nameserver {}) >> /etc/resolv.conf'.format(dns_server)
            res = self.svr2_bms.execute_cmd(cmd, timeout=10)


    def update_vmx_packages(self):
        #check if internet is reachable(8.8.8.8, www.google.com"
        waiting(5)
        cmd = 'ping -c 5 %s'%(internet_www)
        if self.svr2_bms.status_command(cmd) != 0:
            print "\n"
            print "Command: %s"%(cmd)
            print "========================================================================="
            print "Internet URL({0}) is not reachable from {1}, please check route and DNS".format(internet_www, self.svr2_bms_name)
            print "========================================================================="
            sys.exit(1)
        else:
            print "Internet URL({0}) is reachable from {1}".format(internet_www, self.svr2_bms_name)

        #apt-get update, upgrade kernel
        print "Upgrading packages to install vMX"
        cmd = 'cp /etc/apt/sources.list.save /etc/apt/sources.list'
        self.svr2_bms.execute_cmd(cmd, timeout=10)
        cmd = 'sed -i \'/dl.google/s/^/#/\' /etc/apt/sources.list'
        self.svr2_bms.execute_cmd(cmd, timeout=10)
        cmd = 'sed -i \'/medibuntu/s/^/#/\' /etc/apt/sources.list'
        self.svr2_bms.execute_cmd(cmd, timeout=10)
        cmd = 'apt-get update'
        self.svr2_bms.execute_cmd(cmd, timeout=240)
        cmd = 'apt-get install -y linux-firmware linux-image-3.13.0.32-generic linux-image-extra-3.13.0.32-generic linux-headers-3.13.0.32-generic'
        self.svr2_bms.execute_cmd(cmd, timeout=300)
        cmd = 'update-grub'
        self.svr2_bms.execute_cmd(cmd, timeout=120)
        cmd = 'apt-get install -y expect bridge-utils qemu-kvm libvirt-bin python-netifaces vnc4server libyaml-dev python-yaml numactl libparted0-dev libpciaccess-dev libnuma-dev libyajl-dev libxml2-dev libglib2.0-dev libnl-dev python-pip python-dev libxml2-dev libxslt-dev'
        self.svr2_bms.execute_cmd(cmd, timeout=600)

        #install upgrade libvirt 1.2.19
        print "Upgrading libvirt version to 1.2.19 on %s"%(self.svr2_bms_name)
        self.svr2_bms.chdir('/tmp')
        cmd = 'rm -rf libvirt-1.2.19/'
        self.svr2_bms.execute_cmd(cmd, timeout=10)
        cmd = 'rm -rf libvirt-1.2.19.*'
        self.svr2_bms.execute_cmd(cmd, timeout=10)
        cmd = 'wget -q http://{0}/pockit_images/{1} -O {1}'.format(self.server1_ip,sk_libvirt_img)
        if self.svr2_bms.status_command(cmd) != 0:
            print "\n"
            print "Command: %s"%(cmd)
            print "=================================================="
            print "Error in downloading libvirt image on to %s"%(self.svr2_bms_name)
            print "=================================================="
            sys.exit(1)
        else:
            print "Downloaded libvirt image on to %s successfully"%(self.svr2_bms_name)
        cmd = 'sudo service libvirt-bin stop'
        res = self.svr2_bms.execute_cmd(cmd, timeout=40)
        cmd = 'tar -xzf libvirt-1.2.19.tar.gz'
        self.svr2_bms.execute_cmd(cmd, timeout=40)
        self.svr2_bms.chdir('/tmp/libvirt-1.2.19/')
        cmd = './configure --prefix=/usr --localstatedir=/ --with-numactl'
        self.svr2_bms.execute_cmd(cmd, timeout=300)
        self.svr2_bms.chdir('/tmp/libvirt-1.2.19/')
        cmd = 'make'
        self.svr2_bms.execute_cmd(cmd, timeout=300)
        self.svr2_bms.chdir('/tmp/libvirt-1.2.19/')
        cmd = 'sudo make install && sudo ldconfig'
        self.svr2_bms.execute_cmd(cmd, timeout=300)

        #check if service is running
        cmd = 'sudo service libvirt-bin start'
        self.svr2_bms.execute_cmd(cmd, timeout=10)
        cmd = 'sudo service libvirt-bin status'
        res = self.svr2_bms.execute_cmd(cmd, timeout=10)
        if re.search(r'running', res, re.M|re.I):
            print "libvirt-bin service is running after upgrading to 1.2.19 version"
        else:
            print "\n"
            print "Command: %s"%(cmd)
            print "Response: %s"%(res)
            print "===================================================="
            print "libvirt-bin service is not running! please check it"
            print "===================================================="
            sys.exit(1)

        cmd = 'sudo libvirtd --version'
        res = self.svr2_bms.execute_cmd(cmd, timeout=10)
        if re.search(r'19', res, re.M|re.I):
            print "Upgraded libvirt-bin version 1.2.19 is running"
        else:
            print "\n"
            print "Command: %s"%(cmd)
            print "Response: %s"%(res)
            print "==================================================================="
            print "Upgraded libvirt-bin version 1.2.19 is not running! please check it"
            print "==================================================================="
            sys.exit(1)

    def create_vmx_conf(self, re_img, fpc_img, hdd_img):
        print 'Creating vMX confguration file'
        vmx_conf_template = jinja_env.get_template('vmx.conf')
        user_data = vmx_conf_template.render(
                vmx_hostname = CONF['vmx']['hostname'],
                vmx_ext_intf = CONF['vmx']['management_interface'],
                vmx_re_img = re_img,
                vmx_re_hdd = hdd_img,
                vmx_pfe_img = fpc_img,
                vmx_ctl_ip_addr = get_ip(CONF['DEFAULTS']['vmx_vcp_address']),
                vmx_pfe_ip_addr = get_ip(CONF['DEFAULTS']['vmx_vfp_address'])
                )
        fobj = open(sk_img_path + '/vmx.conf', 'w')
        if hasattr(user_data, '__iter__'):
            for data in user_data:
                fobj.write(data)
            fobj.close()
        else:
            fobj.write(user_data)
            fobj.close()

    def create_vmx_junosdev_conf(self):
        print 'Creating vmx-junosdev file'
        dev_conf_template = jinja_env.get_template('vmx-junosdev.conf')
        user_data = dev_conf_template.render(
                vmx_hostname = CONF['vmx']['hostname'],
                ctrldata_intf = CONF['vmx']['ctrldata_interface']
                )
        fobj = open(sk_img_path + '/vmx-junosdev.conf', 'w')
        if hasattr(user_data, '__iter__'):
            for data in user_data:
                fobj.write(data)
            fobj.close()
        else:
            fobj.write(user_data)
            fobj.close()

    def create_vmx_default_config(self):
        print 'Creating vMX init configuration expect script file'
        init_conf_template = jinja_env.get_template('vmx-init-cfg.exp')
        user_data = init_conf_template.render(
                vmx_hostname = CONF['vmx']['hostname'],
                vmx_root_passwd = CONF['DEFAULTS']['root_password'],
                vmx_dns_server = CONF['vmx']['dns_servers'],
                vmx_ntp_server = CONF['DEFAULTS']['ntp_servers'],
                vmx_ctrldata_ip = CONF['vmx']['ctrldata_address'],
                vmx_re_ipaddr = CONF['DEFAULTS']['vmx_vcp_address'],
                vmx_default_gateway = CONF['DEFAULTS']['vmx_ext_gateway'],
                vmx_loopback_ip = CONF['DEFAULTS']['vmx_loopback_ip']
                )
        fobj = open(sk_img_path + '/vmx-init-cfg.exp', 'w')
        if hasattr(user_data, '__iter__'):
            for data in user_data:
                fobj.write(data)
            fobj.close()
        else:
            fobj.write(user_data)
            fobj.close()

    def pre_up_vmx(self):
        print 'Preparing the infra-structure for installing vMX'
	global vmx_folder

        vmximage = CONF['DEFAULTS']['vmximage']
        vmx_base_folder = '{0}/images/'.format(self.svr2_bms_home)
        self.svr2_bms.chdir(vmx_base_folder)
        cmd = 'tar -xzf %s'%(vmximage)
        self.svr2_bms.execute_cmd(cmd, timeout=60)

        fname,fext = os.path.splitext(vmximage)
        vmx_folder_name = 'vmx-'+fname.split('-')[2]
        vmx_img_folder = vmx_base_folder+vmx_folder_name+'/images/'
        vmx_folder = vmx_base_folder+vmx_folder_name+'/'
        self.svr2_bms.chdir(vmx_img_folder)

        cmd = 'ls | grep -E \'qcow2|vFPC\''
        op = self.svr2_bms.execute_cmd(cmd, timeout=60)
        vmx_img_list = op.splitlines()
        re_img = vmx_img_folder+vmx_img_list[0]
        fpc_img = vmx_img_folder+vmx_img_list[1]
        hdd_img = vmx_img_folder+'vmxhdd.img'

        self.create_vmx_conf(re_img, fpc_img, hdd_img)
        self.create_vmx_junosdev_conf()
        self.create_vmx_default_config()

        self.svr2_bms.chdir(vmx_folder+'config/')
        jumphost_img_url = 'http://{}/pockit_images/'.format(self.server1_ip)
        cmd = 'wget -O vmx.conf {0}vmx.conf'.format(jumphost_img_url)
        if self.svr2_bms.status_command(cmd) != 0:
            print "\n"
            print "Command: %s"%(cmd)
            print "===================================================="
            print "Error in downloading vmx.conf file onto server %s"%(self.svr2_bms_name)
            print "===================================================="
            sys.exit(1)
        else:
            print "Successfully downloaded vmx.conf file onto server %s"%(self.svr2_bms_name)

        cmd = 'wget -O vmx-junosdev.conf {0}vmx-junosdev.conf'.format(jumphost_img_url)
        if self.svr2_bms.status_command(cmd) != 0:
            print "\n"
            print "Command: %s"%(cmd)
            print "============================================================"
            print "Error in downloading vmx-junosdev.conf file onto server %s"%(self.svr2_bms_name)
            print "============================================================"
            sys.exit(1)
        else:
            print "Successfully downloaded vmx-junosdev.conf file onto server %s"%(self.svr2_bms_name)

        self.svr2_bms.chdir(vmx_folder)
        cmd = 'wget -O vmx-init-cfg.exp {0}vmx-init-cfg.exp'.format(jumphost_img_url)
        if self.svr2_bms.status_command(cmd) != 0:
            print "\n"
            print "Command: %s"%(cmd)
            print "============================================================"
            print "Error in downloading vmx-init-cfg.exp file onto server %s"%(self.svr2_bms_name)
            print "============================================================"
            sys.exit(1)
        else:
            print "Successfully downloaded vmx-init-cfg.exp file onto server %s"%(self.svr2_bms_name)


    def deploy_vmx(self):
        print "Deploying vMX. Begins.."
	global vmx_folder
        self.svr2_bms.chdir(vmx_folder)

        #delete virbr0 bridge
        cmd = '/sbin/ifconfig virbr0 down'
        self.svr2_bms.execute_cmd(cmd, timeout=10)

        cmd = 'brctl delbr virbr0'
        self.svr2_bms.execute_cmd(cmd, timeout=10)

        cmd = 'sudo ./vmx.sh -lv --install'
        res = self.svr2_bms.execute_cmd(cmd, timeout=360)
        if 'error(s)' in res:
            print "\n"
            print "Command: %s"%(cmd)
            print "Response: %s"%(res)
            print "======================================"
            print "Error in deploying vMX onto server %s"%(self.svr2_bms_name)
            print "======================================"
            sys.exit(1)
        else:
            print "Successfully deployed vMX onto server %s"%(self.svr2_bms_name)

        cmd = 'sudo ./vmx.sh --bind-dev'
        op = self.svr2_bms.execute_cmd(cmd, timeout=40)

        cmd = 'sudo ./vmx.sh --bind-check'
        op = self.svr2_bms.execute_cmd(cmd, timeout=40)
        print "Deploying vMX. Ends.."

    def configure_and_verify_vmx(self):
        print "Configuring and verifying vMX management IP reachability"
        print "Waiting for vMX to boot up.."

        waiting(200)
	global vmx_folder
        self.svr2_bms.chdir(vmx_folder)

        cmd = 'expect vmx-init-cfg.exp'
        op = self.svr2_bms.execute_cmd(cmd, timeout=120)
        waiting(10)

        vmx_re_ipaddr = get_ip(CONF['DEFAULTS']['vmx_vcp_address'])
        cmd = 'ping -c 5 {}'.format(vmx_re_ipaddr)
        res = self.svr2_bms.execute_cmd(cmd, timeout=60)
        if re.search(r'5 received', res, re.M|re.I):
            print "vMX mgmt IP %s is reachable, its UP and RUNNING"%(vmx_re_ipaddr)
        else:
            print "\n"
            print "Command: %s"%(cmd)
            print "Response: %s"%(res)
            print "============================================================"
            print "vMX mgmt IP %s is not reachable, please check configuration!"%(vmx_re_ipaddr)
            print "============================================================"
            sys.exit(1)

        svr2 = self.svr2_bms_name
        print "Configuring ctrldata interface with IP on server(%s)"%(self.svr2_bms_name)
        cmd = '/sbin/ifconfig {0} 0.0.0.0 up'.format(CONF[svr2]['ctrldata_interface'])
        res = self.svr2_bms.execute_cmd(cmd, timeout=20)

        ctl_ip = get_ip(CONF[svr2]['ctrldata_address'])
        ctl_netmask = get_netmask(CONF[svr2]['ctrldata_address'])
        cmd = '/sbin/ifconfig vmx_link1 {0} netmask {1} up'.format(ctl_ip, ctl_netmask)
        res = self.svr2_bms.execute_cmd(cmd, timeout=20)
        waiting(5)

        vmx_loopback_ip = CONF['DEFAULTS']['vmx_loopback_ip']
        vmx_ctl_ip = get_ip(CONF['vmx']['ctrldata_address'])
        cmd = 'route add -host {0}/32 gw {1}'.format(vmx_loopback_ip,vmx_ctl_ip)
        res = self.svr2_bms.execute_cmd(cmd, timeout=40)
        waiting(10)

        #on jumphost add route to vmx loopback ip 
        command = 'route add -host {0}/32 gw {1}'.format(vmx_loopback_ip,ctl_ip)
        res = execute(command, ignore_errors=False)
        waiting(5)

        #ping vMX loopback ip
        print "Checking vmx_loopback IP(%s) is reachable"%(vmx_loopback_ip)
        cmd = 'ping -c 5 {}'.format(vmx_loopback_ip)
        res = self.svr2_bms.execute_cmd(cmd, timeout=60)
        if re.search(r'5 received', res, re.M|re.I):
            print "vMX loopback IP %s is reachable from server(%s)"%(vmx_loopback_ip, svr2)
        else:
            print "\n"
            print "Command: %s"%(cmd)
            print "Response: %s"%(res)
            print "=========================================================================="
            print "vMX loopback IP %s is not reachable from server(%s). Check configuration."%(vmx_loopback_ip, svr2)
            print "=========================================================================="
            sys.exit(1)
