import os
import sys
import psutil
from oslo_config import cfg
from utils import helpers
from utils.configsk import configSetup
from deploy_vmx import vmxDeploy

CONF = cfg.CONF
SK_ENV_FILE = 'pockit_env.conf'

class deployVMX(object):

    def init():
        pass

    def check_config_file_exists(self):
        install_dir = helpers.from_project_root('conf/')
        cfile = os.path.join(install_dir, SK_ENV_FILE)
        if not os.path.exists(os.path.join(install_dir, SK_ENV_FILE)):
            print "Missing required configuration file {}".format(cfile)
            sys.exit(1)
        print "Configuration file {} exists".format(cfile)


if __name__ == '__main__':

    # Do prep work 
    vmx = deployVMX()
    vmx.check_config_file_exists()

    #load config file
    config = configSetup()
    config.set_base_config_options()

    try:
        config.load_configs(['conf/{}'.format(SK_ENV_FILE)])
        print "Loaded configuration file successfully"
    except cfg.RequiredOptError as e:
        print "Missing required input in pockit_env.conf file, {0}: {1}".format(SK_ENV_FILE, e)
        sys.exit(1)

    config.set_deploy_virtual_server_config_options()
    config.set_deploy_physical_server_config_options()

    #vmx deploy 
    vmx = vmxDeploy()
    vmx.copy_vmx_images()
    vmx.configure_server_uplink_intf()
    vmx.update_vmx_packages()
    vmx.pre_up_vmx()
    vmx.deploy_vmx()
    vmx.configure_and_verify_vmx()
    sys.exit(0)
