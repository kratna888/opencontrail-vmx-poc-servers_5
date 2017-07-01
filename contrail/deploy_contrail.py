import os
import sys
import json
import re
from oslo_config import cfg
from jinja2 import Environment, FileSystemLoader
from netaddr import IPNetwork
from time import sleep, strftime
import Queue
import threading

from utils.helpers import execute, from_project_root, get_project_root
from utils.remoteoperations import RemoteConnection
from utils.servermanager import serverManager

CONF = cfg.CONF

sk_img_path = '/var/www/html/pockit_images'
ubuntu_img_id = 'ubuntu-14-04-4'
sk_cluster_id = 'pockitcluster'
contrail_pkg_id = 'mitaka'

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

class reImageAndDeploy(object):

    def __init__(self):
        global jinja_env
        jinja_env = Environment(loader=FileSystemLoader(from_project_root('contrail')))
        self.sm_vm = None
        self.sm_api = None
        self.sm_home = None

    def check_sm_status(self):
        print "Checking servermanager reachablility."
        sm_m_ip = get_ip(CONF['servermanager']['management_address'])
        sm_local_user = CONF['servermanager']['local_user']
        sm_local_passwd = CONF['servermanager']['local_password']

        count = 5
        for _ in range(count):
            #ping SM
            res = os.system('ping -c 3 ' + sm_m_ip)
            if res == 0:
                print "servermanager is reachable"
                self.sm_vm = RemoteConnection()
                self.sm_vm.connect(sm_m_ip, username=sm_local_user, password=sm_local_passwd)
                self.sm_api = serverManager(sm_m_ip)
                cmd = 'eval echo ~$USER'
                self.sm_home = self.sm_vm.execute_cmd(cmd, timeout=10)
                return
            else:
                print "Waiting for servermanager to respond."
                print "Retry after 30sec"
                waiting(30)
        print "\n"
        print "Response: %s"%(res)
        print "=================================================================================="
        print "servermanager is not reachable, please check or run ./smgr_provision.sh if not done"
        print "=================================================================================="
        sys.exit(1)

    def copy_iso_contrail_images(self):
        print "Copying ubuntu OS ISO and contrail installer package onto servermanager VM"        

        svr1 = CONF['DEFAULTS']['bms'][0]
        server1_mgmt_ip = get_ip(CONF[svr1]['management_address'])
        sm_m_ip = get_ip(CONF['servermanager']['management_address'])
        ubuntuiso = CONF['DEFAULTS']['ubuntuimage']
        contrailimg = CONF['DEFAULTS']['contrailimage']

        jumphost_img_url = 'http://{}/pockit_images/'.format(server1_mgmt_ip)

        self.sm_vm.chdir('{}/images/'.format(self.sm_home))
        cmd = 'wget -q {0}{1} -O {1}'.format(jumphost_img_url,ubuntuiso)
        if self.sm_vm.status_command(cmd) != 0:
            print "\n"
            print "Command: %s"%(cmd)
            print "=========================================================="
            print "Error in downloading ubuntu ISO image onto server-manager"
            print "=========================================================="
            sys.exit(1)
        else:
            print "Downloaded ubuntu ISO image successfully onto server-manager"

        cmd = 'wget -q {0}{1} -O {1}'.format(jumphost_img_url,contrailimg)
        if self.sm_vm.status_command(cmd) != 0:
            print "\n"
            print "Command: %s"%(cmd)
            print "==================================================================="
            print "Error in downloading contrail installer package onto server-manager"
            print "==================================================================="
            sys.exit(1)
        else:
            print "Downloaded contrail installer package successfully onto server-manager"


    def create_cluster_json_file(self):
        print "Creating contrail cluster json file"        
        cluster_tmpl = jinja_env.get_template('cluster.json')
        userdata = cluster_tmpl.render(
            dns_search=CONF['DEFAULTS']['dns_search'],
            cluster_gateway=CONF['DEFAULTS']['cluster_gateway'],
            contrail_asn=CONF['DEFAULTS']['contrail_asn'],
            openstack_password=CONF['DEFAULTS']['contrail_os_webui_passwd'],
            mysql_password=CONF['DEFAULTS']['contrail_mysql_passwd'],
            cassandra_db_size = CONF['DEFAULTS']['cassandra_db_size'],
            cluster_netmask=CONF['DEFAULTS']['cluster_netmask']
        )
        with open(sk_img_path + '/cluster.json', 'w+') as fobj:
            fobj.write(userdata)

    def create_image_json_file(self):
        print "Creating cluster host os image and contrail installer package file"        
        img_tmpl = jinja_env.get_template('image.json')
        userdata = img_tmpl.render(
            kickseed_file=self.sm_home+'/json-files/contrail-ubuntu-sk.seed',
            kickstart_file=self.sm_home+'/json-files/contrail-ubuntu-sk.ks',
            ubuntu_iso_file=self.sm_home+'/images/'+CONF['DEFAULTS']['ubuntuimage'],
            contrail_pkg_file=self.sm_home+'/images/'+CONF['DEFAULTS']['contrailimage']
        )
        with open(sk_img_path + '/image.json', 'w+') as fobj:
            fobj.write(userdata)

    def create_vm_server_json_files(self):
        vm_list = CONF['DEFAULTS']['reimagevms']
        print "Creating VM %s json files"%(vm_list)        
        filenum = 1
        for vm in vm_list:
            vm_tmpl = jinja_env.get_template('server.json')
            userdata = vm_tmpl.render(
                    ctrldata_intf = CONF[vm]['ctrldata_interface'],
                    dns_search = CONF[vm]['dns_search'],
                    server_name = CONF[vm]['hostname'],
                    ipmi_address = "",
                    ipmi_password = "",
                    ipmi_username = "",
                    mgmt_address = CONF[vm]['management_address'],
                    mgmt_mac = CONF[vm]['management_mac'],
                    mgmt_intf = CONF[vm]['management_interface'],
                    mgmt_gateway = CONF[vm]['gateway'],
                    ctrldata_address = CONF[vm]['ctrldata_address'],
                    ctrldata_mac = CONF[vm]['ctrldata_mac'],
                    disk_partition = CONF[vm]['partition'],
                    root_password = CONF['DEFAULTS']['root_password'],
                    roles = CONF[vm]['roles']
                )
            with open(sk_img_path + '/server{}.json'.format(filenum), 'w+') as fobj:
                fobj.write(userdata)
            filenum += 1

    def create_bms_server_json_files(self):
        vm_list = CONF['DEFAULTS']['reimagevms']
        bms_list = CONF['DEFAULTS']['bms']
        print "Creating BMS %s json files"%(bms_list)        
        filenum = len(vm_list) + 1
        for vm in bms_list[1:]:
            vm_tmpl = jinja_env.get_template('server.json')
            userdata = vm_tmpl.render(
                    ctrldata_intf = CONF[vm]['ctrldata_interface'],
                    dns_search = CONF[vm]['dns_search'],
                    server_name = CONF[vm]['hostname'],
                    ipmi_address = CONF[vm]['ipmi_address'],
                    ipmi_password = CONF[vm]['ipmi_password'],
                    ipmi_username = CONF[vm]['ipmi_username'],
                    mgmt_address = CONF[vm]['management_address'],
                    mgmt_mac = CONF[vm]['management_mac'],
                    mgmt_intf = CONF[vm]['management_interface'],
                    mgmt_gateway = CONF[vm]['gateway'],
                    ctrldata_address = CONF[vm]['ctrldata_address'],
                    ctrldata_mac = CONF[vm]['ctrldata_mac'],
                    disk_partition = CONF[vm]['partition'],
                    root_password = CONF['DEFAULTS']['root_password'],
                    roles = CONF[vm]['roles']
                )
            with open(sk_img_path + '/server{}.json'.format(filenum), 'w+') as fobj:
                fobj.write(userdata)
            filenum += 1

    def copy_seed_ks_to_websvr(self):
        print "Copying ubuntu seed and kickstart files on to jumphost webserver"        
        cmd = 'cp {0}/contrail-ubuntu-sk.seed {1}/'.format(from_project_root('contrail'),sk_img_path)
        execute(cmd, ignore_errors=True)
        cmd = 'cp {0}/contrail-ubuntu-sk.ks {1}/'.format(from_project_root('contrail'),sk_img_path)
        execute(cmd, ignore_errors=True)

    def copy_json_files_to_sm(self):
        print "Copying cluster, image, VM and BMS json files on to server-manager VM"        
        svr1 = CONF['DEFAULTS']['bms'][0]
        server1_mgmt_ip = get_ip(CONF[svr1]['management_address'])
        jumphost_img_url = 'http://{}/pockit_images/'.format(server1_mgmt_ip)

        cmd = 'cd {0}/json-files/ && wget -O cluster.json {1}/cluster.json'.format(self.sm_home, jumphost_img_url)
        if self.sm_vm.status_command(cmd) != 0:
            print "\n"
            print "Command: %s"%(cmd)
            print "============================================================"
            print "Error in downloading cluster.json file onto servermanager VM"
            print "============================================================"
            sys.exit(1)
        else:
            print "Downloaded cluster.json file successfully onto servermanager VM"

        cmd = 'cd {0}/json-files/ && wget -O image.json {1}/image.json'.format(self.sm_home, jumphost_img_url)
        if self.sm_vm.status_command(cmd) != 0:
            print "\n"
            print "Command: %s"%(cmd)
            print "============================================================"
            print "Error in downloading image.json file onto servermanager VM"
            print "============================================================"
            sys.exit(1)
        else:
            print "Downloaded image.json file successfully onto servermanager VM"

        cluster_list = CONF['DEFAULTS']['reimagevms'] + CONF['DEFAULTS']['bms'][1:]
        for i in range(1,len(cluster_list)+1):
            cmd = 'cd {0}/json-files/ && wget -O server{1}.json {2}/server{1}.json'.format(self.sm_home, i, jumphost_img_url)
            if self.sm_vm.status_command(cmd) != 0:
                print "\n"
                print "Command: %s"%(cmd)
                print "============================================================="
                print "Error in downloading server{}.json file onto servermanager VM".format(i)
                print "============================================================"
                sys.exit(1)
            else:
                print "Downloaded server{}.json file successfully onto servermanager VM".format(i)

        self.copy_seed_ks_to_websvr()
        cmd = 'cd {0}/json-files/ && wget -O contrail-ubuntu-sk.ks {1}/contrail-ubuntu-sk.ks'.format(self.sm_home, jumphost_img_url)
        if self.sm_vm.status_command(cmd) != 0:
            print "\n"
            print "Command: %s"%(cmd)
            print "============================================================"
            print "Error in downloading kickstart file onto servermanager VM"
            print "============================================================"
            sys.exit(1)
        else:
            print "Downloaded kickstart file successfully onto servermanager VM"

        cmd = 'cd {0}/json-files/ && wget -O contrail-ubuntu-sk.seed {1}/contrail-ubuntu-sk.seed'.format(self.sm_home, jumphost_img_url)
        if self.sm_vm.status_command(cmd) != 0:
            print "\n"
            print "Command: %s"%(cmd)
            print "============================================================"
            print "Error in downloading kickseed file onto servermanager VM"
            print "============================================================"
            sys.exit(1)
        else:
            print "Downloaded kickseed file successfully onto servermanager VM"


    def reimage_vms(self):
        vm_list = CONF['DEFAULTS']['reimagevms']
        print "Re-Imaging %s VM's. Begins.."%(vm_list)

        #add cluster, image and server1,2
        print "Adding cluster.json to servermanager"
        cmd = 'server-manager delete cluster --cluster_id {}'.format(sk_cluster_id)
        self.sm_vm.execute_cmd(cmd, timeout=30)
        cmd = 'server-manager add cluster -f {}/json-files/cluster.json'.format(self.sm_home)
        self.sm_vm.execute_cmd(cmd, timeout=30)
        res = self.sm_api.cluster_get(sk_cluster_id)
        if res != 200:
            print "\n"
            print "Command: %s"%(cmd)
            print "Response: %s"%(res)
            print "======================================"
            print "Error in adding cluster.json to servermanager"
            print "======================================"
            sys.exit(1)
        else:
            print "Successfully added cluster.json to servermanager"

        print "Adding image.json to servermanager"
        cmd = 'server-manager delete image --image_id {}'.format(ubuntu_img_id)
        self.sm_vm.execute_cmd(cmd, timeout=120)
        cmd = 'server-manager add image -f {}/json-files/image.json'.format(self.sm_home)
        self.sm_vm.execute_cmd(cmd, timeout=120)
        res = self.sm_api.image_get(ubuntu_img_id)
        if res != 200:
            print "\n"
            print "Command: %s"%(cmd)
            print "Response: %s"%(res)
            print "======================================"
            print "Error in adding image.json to servermanager"
            print "======================================"
            sys.exit(1)
        else:
            print "Successfully added image.json to servermanager"

        vm_list = CONF['DEFAULTS']['reimagevms']
        i = 1
        for vm in vm_list:
            print "Adding server{}.json to servermanager".format(i)
            cmd = 'server-manager delete server --server_id {}'.format(CONF[vm]['hostname'])
            self.sm_vm.execute_cmd(cmd, timeout=30)
            cmd = 'server-manager add server -f {0}/json-files/server{1}.json'.format(self.sm_home, i)
            self.sm_vm.execute_cmd(cmd, timeout=30)
            res = self.sm_api.server_get(CONF[vm]['hostname'])
            if res != 200:
                print "\n"
                print "Command: %s"%(cmd)
                print "Response: %s"%(res)
                print "============================================="
                print "Error in adding server{}.json to servermanager".format(i)
                print "============================================="
                sys.exit(1)
            else:
                print "Successfully added server{}.json to servermanager".format(i)
                print "Issuing reimage server{}".format(i)
                cmd = 'yes y | server-manager reimage --server_id {}'.format(CONF[vm]['hostname'])
                self.sm_vm.execute_cmd(cmd, timeout=10)
                i += 1

        for vm in vm_list:
            print "Re-imaging  %s  VM" %CONF[vm]['hostname']
            cmd = "virsh destroy %s" %CONF[vm]['hostname']
            execute(cmd, ignore_errors=True)
            waiting(5)
            cmd = "virsh undefine %s" %CONF[vm]['hostname']
            execute(cmd, ignore_errors=True)
            waiting(5)
            cmd = "virt-install --os-variant=ubuntutrusty --os-type=linux --arch=x86_64 --network mac=%s,bridge=mgmtbr --network mac=%s,bridge=ctrldatabr  --file=/var/lib/libvirt/images/%s.qcow2 --graphics vnc,password=%s --noautoconsole --vcpus=%s --ram=%s --pxe --name %s --autostart --file-size=%s" %(CONF[vm]['management_mac'], CONF[vm]['ctrldata_mac'], CONF[vm]['hostname'], CONF[vm]['local_password'], CONF[vm]['vcpus'], CONF[vm]['memory'], CONF[vm]['hostname'], CONF[vm]['harddisk'])
            execute(cmd, ignore_errors=False)
            waiting(10)
            print "Waiting for %s VM to reimage" %vm
        self.check_reimage_status(vm_list)
        print "Re-Imaging %s VM's. Ends.."%vm_list

    def check_reimage_status(self, svr_list):
        queue = Queue.Queue()
        threads = []
        for vm in svr_list:
            svr = CONF[vm]['hostname']
            t = threading.Thread(target=self.check_reimage_vm_status, args=(svr,), name=svr)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        print "Completed VM re-image status verification"

    def check_reimage_vm_status(self, vmname):
        count = 40
        expected_res = "reimage_completed"
        for _ in range(count):
            #Get reimage status
            res = self.sm_api.server_get(server_id=vmname, status=True)
            print "Waiting for %s VM to get re-imaged.."%vmname
            print "Expected status:   %s      ::      Current status:  %s  "%(expected_res, res)
            if res == expected_res:
                waiting(10)
                cmd = "virsh list --all"
                res = execute(cmd, ignore_errors=True)
                if re.search(r'shut off',res,re.M|re.I):
                    print "Re-imaged VM is shut-off, starting now..."
                    cmd = "virsh start "+vmname
                    res = execute(cmd, ignore_errors=True)
                    waiting(5)
                else: 
                    print "None of the re-imaged VM's are shut-off"
                return
            else:
                print "Retry after 30sec"
                waiting(30)
        print "\n"
        print "==========================================================="
        print "VM %s did not complete Re-imaging, hence exiting..." %vmname
        print "==========================================================="
        sys.exit(1)

    def check_ipmi_connectivity(self):
        print "Checking IPMI reachability before re-imaging BMS"
        waiting(10)
        bms_list = CONF['DEFAULTS']['bms']
        for bms in bms_list:
            ipmi_ip = CONF[bms]['ipmi_address']
            cmd = 'ping -c 4 %s'%(ipmi_ip)
            res = self.sm_vm.execute_cmd(cmd, timeout=10)
            if re.search(r'4 received', res, re.M|re.I):
                print "IPMI IP (%s) of BMS (%s) is reachable from servermanager."%(ipmi_ip,bms)
            else:
                print "\n"
                print "Command: %s"%(cmd)
                print "Response: %s"%(res)
                print "=============================================================================="
                print "IPMI IP (%s) of BMS (%s) is not reachable from servermanager.check IPMI config"%(ipmi_ip,bms)
                print "=============================================================================="
                sys.exit(1)


    def reimage_bms(self):
        print "Re-Imaging BMS's. Begins.."

        #check ipmi
        self.check_ipmi_connectivity()

        bms_list = CONF['DEFAULTS']['bms']
        vms_list = CONF['DEFAULTS']['reimagevms']
        i = len(vms_list) + 1
        for bms in bms_list[1:]:
            print "Adding server{}.json to servermanager".format(i)
            cmd = 'server-manager delete server --server_id {}'.format(CONF[bms]['hostname'])
            self.sm_vm.execute_cmd(cmd, timeout=30)
            cmd = 'server-manager add server -f {0}/json-files/server{1}.json'.format(self.sm_home, i)
            self.sm_vm.execute_cmd(cmd, timeout=30)
            res = self.sm_api.server_get(CONF[bms]['hostname'])
            if res != 200:
                print "\n"
                print "Command: %s"%(cmd)
                print "Response: %s"%(res)
                print "============================================="
                print "Error in adding server{}.json to servermanager".format(i)
                print "============================================="
                sys.exit(1)
            else:
                print "Successfully added server{}.json to servermanager".format(i)
                print "Issuing reimage server{}".format(i)
                cmd = 'yes y | server-manager reimage --server_id {}'.format(CONF[bms]['hostname'])
                self.sm_vm.execute_cmd(cmd, timeout=10)
                i += 1
        print "Waiting for BMS's to get reimaged"
        self.check_bms_reimage_status(bms_list[1:])
        print "Re-Imaging %s BMS's. Ends.."%bms_list[1:]

    def check_bms_reimage_status(self, svr_list):
        queue = Queue.Queue()
        threads = []
        for bms in svr_list:
            svr = CONF[bms]['hostname']
            t = threading.Thread(target=self.check_reimage_bms_status, args=(svr,), name=svr)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        print "Completed reimage status verification for BMS"

    def check_reimage_bms_status(self, bmsname):
        count = 80
        expected_res = "reimage_completed"
        for _ in range(count):
            #Get reimage status
            res = self.sm_api.server_get(server_id=bmsname, status=True)
            print "Waiting for BMS %s to re-image.."%bmsname
            print "Expected status:   %s   ::  Current status:  %s  "%(expected_res, res)
            if res == expected_res:
                print "BMS %s is re-imaged"%bmsname
                return
            else:
                print "Retry after 30sec"
                waiting(30)
                if count == 30 and res == "restart_issued":
                    print "Looks like some issue with BMS (%s) powering on, please check"%(bmsname)
                    continue
        print "\n"
        print "==============================================================="
        print "BMS server %s did not complete Re-imaging, hence exiting..." %bmsname
        print "==============================================================="
        sys.exit(1)

    def wait_bms_reboot(self):
        bms_list = CONF['DEFAULTS']['bms']
        for bms in bms_list[1:]:
            count = 40
            for _ in range(count):
                bms_ip = get_ip(CONF[bms]['management_address'])
                cmd = 'ping -c 4 %s'%(bms_ip)
                res = self.sm_vm.execute_cmd(cmd, timeout=30)
                if re.search(r'4 received', res, re.M|re.I):
                    print "MGMT IP(%s)of (%s) is reachable from servermanager."%(bms_ip,bms)
                    continue
                else:
                    print "Waiting for the BMS %s to reboot and come up"%bms
                    print "Retry after 30sec"
                    waiting(30)

    def check_topology_connectivity(self):
        print "Checking the management IP and ctrldata IP reachability from servermanager VM"
        waiting(5)
        all_list = CONF['DEFAULTS']['bms'] + CONF['DEFAULTS']['reimagevms']
        for inventory in all_list:
            mgmt_ip = get_ip(CONF[inventory]['management_address'])
            cmd = 'ping -c 4 %s'%(mgmt_ip)
            res = self.sm_vm.execute_cmd(cmd, timeout=30)
            if re.search(r'4 received', res, re.M|re.I):
                print "MGMT IP(%s)of (%s) is reachable from servermanager."%(mgmt_ip,inventory)
            else:
                print "\n"
                print "Command: %s"%(cmd)
                print "Response: %s"%(res)
                print "=============================================================================="
                print "MGMT IP(%s)of (%s) is not reachable from servermanager.check configuration"%(mgmt_ip, inventory)
                print "=============================================================================="
                sys.exit(1)
        #TO DO check connectivity from cc-host VM, os-host, BMS


    def remove_server2_from_cluster(self):

        bms2 = CONF['DEFAULTS']['bms'][1]
        print "\nRemoving %s from contrail cluster, as it will be used to deploy vMX on it"%(bms2)

        svr2_ip = get_ip(CONF[bms2]['management_address'])
        svr2_netmask = get_netmask(CONF[bms2]['management_address'])
        svr2_usr = CONF['DEFAULTS']['root_username']
        svr2_passwd = CONF['DEFAULTS']['root_password']
        svr2_connect = RemoteConnection()
        svr2_connect.connect(svr2_ip, username=svr2_usr, password=svr2_passwd)

        #reboot
        bms2_name = CONF[bms2]['hostname']
        print "Rebooting %s"%(bms2_name)
        cmd = 'reboot'
        svr2_connect.execute_cmd(cmd, timeout=10)
        waiting(10)

        print "Deleting %s from the cluster"%(bms2_name)
        cmd = 'server-manager delete server --server_id %s'%(bms2_name)
        res = self.sm_vm.execute_cmd(cmd, timeout=10)

        #wait till the bms to comes up
        count = 40
        for _ in range(count):
            #ping server2
            res = os.system('ping -c 3 ' + svr2_ip)
            if res == 0:
                print "%s is reachable and is UP"%(bms2_name)
                waiting(60)
                return
            else:
                print "Waiting for %s to come UP.."%(bms2_name)
                print "Retry after 30sec"
                waiting(30)
        print "\n"
        print "========================================================================="
        print "{0} is not reachable, did not boot up with correct mgmt_address {1}".format(bms2_anme,svr2_ip)
        print "========================================================================="
        sys.exit(1)


    def provision_contrail(self):
        print "Started provisioning contrail on to the cluster"
        waiting(5)
        provision_list = CONF['DEFAULTS']['reimagevms'] + CONF['DEFAULTS']['bms'][2:]

        self.check_topology_connectivity()
        self.remove_server2_from_cluster()

        cmd = 'yes y | server-manager provision --cluster_id {0} {1}'.format(sk_cluster_id, contrail_pkg_id)
        res = self.sm_vm.execute_cmd(cmd, timeout=30)
        self.check_provision_status(provision_list)
        sys.exit(0)

    def check_provision_status(self, provision_list):
        queue = Queue.Queue()
        threads = []
        for svr in provision_list:
            svr = CONF[svr]['hostname']
            t = threading.Thread(target=self.provision_status, args=(svr,), name=svr)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        print "Completed contrail provision status verification"

    def provision_status(self, bmsname):
        count = 300
        expected_res = "provision_completed"
        for _ in range(count):
            #Get provision status
            res = self.sm_api.server_get(server_id=bmsname, status=True)
            print "Waiting for BMS %s to get provisioned on %s."%(bmsname, sk_cluster_id)
            print "Expected status:   %s        ::      Current status:  %s  "%(expected_res, res)
            if res == expected_res:
                print "BMS %s is provisioned"%bmsname
                return
            else:
                print "Retry after 30sec"
                waiting(30)
        print "\n"
        print "================================================="
        print "BMS %s did not get provisioned, hence exiting..." %bmsname
        print "================================================="
        sys.exit(1)
