import binascii
from twisted.internet import defer

import lib.settings as settings
from stratum.services import GenericService, admin
from stratum.pubsub import Pubsub
from interfaces import Interfaces
from subscription import MiningSubscription
from lib.exceptions import SubmitException

import lib.logger
log = lib.logger.get_logger('mining')
                
class MiningService(GenericService):
    '''This service provides public API for Stratum mining proxy
    or any Stratum-compatible miner software.
    
    Warning - any callable argument of this class will be propagated
    over Stratum protocol for public audience!'''
    
    service_type = 'mining'
    service_vendor = 'stratum'
    is_default = True
    
    @admin
    def update_block(self):
        '''Connect this RPC call to 'litecoind -blocknotify' for 
        instant notification about new block on the network.
        See blocknotify.sh in /scripts/ for more info.'''
        
        log.info("New block notification received")
        Interfaces.template_registry.update_block()
        return True 

    @admin
    def add_litecoind(self, *args):
        ''' Function to add a litecoind instance live '''
        if len(args) != 4:
            raise SubmitException("Incorrect number of parameters sent")

        #(host, port, user, password) = args
        Interfaces.template_registry.bitcoin_rpc.add_connection(args[0], args[1], args[2], args[3])
        log.info("New litecoind connection added %s:%s" % (args[0], args[1]))
        return True 
    
    def authorize(self, worker_name, worker_password):
        '''Let authorize worker on this connection.'''
        
        session = self.connection_ref().get_session()
        session.setdefault('authorized', {})
        
        if Interfaces.worker_manager.authorize(worker_name, worker_password):
            session['authorized'][worker_name] = worker_password
            return True
        else:
            if worker_name in session['authorized']:
                del session['authorized'][worker_name]
            return False
        
    def subscribe(self, *args):
        '''Subscribe for receiving mining jobs. This will
        return subscription details, extranonce1_hex and extranonce2_size'''
        
        extranonce1 = Interfaces.template_registry.get_new_extranonce1()
        extranonce2_size = Interfaces.template_registry.extranonce2_size
        extranonce1_hex = binascii.hexlify(extranonce1)
        
        session = self.connection_ref().get_session()
        session['extranonce1'] = extranonce1
        session['difficulty'] = settings.POOL_TARGET  # Following protocol specs, default diff is 1

        return Pubsub.subscribe(self.connection_ref(), MiningSubscription()) + (extranonce1_hex, extranonce2_size)
        
    def submit(self, worker_name, job_id, extranonce2, ntime, nonce):
        '''Try to solve block candidate using given parameters.'''
        
        session = self.connection_ref().get_session()
        session.setdefault('authorized', {})
        
        # Check if worker is authorized to submit shares
        if not Interfaces.worker_manager.authorize(worker_name, session['authorized'].get(worker_name)):
            raise SubmitException("Worker is not authorized")

        # Check if extranonce1 is in connection session
        extranonce1_bin = session.get('extranonce1', None)
        
        if not extranonce1_bin:
            raise SubmitException("Connection is not subscribed for mining")
        
        difficulty = session['difficulty']
        submit_time = Interfaces.timestamper.time()
        ip = self.connection_ref()._get_ip()
    
        Interfaces.share_limiter.submit(self.connection_ref, job_id, difficulty, submit_time, worker_name)
            
        # This checks if submitted share meet all requirements
        # and it is valid proof of work.
        try:
            (block_header, block_hash, share_diff, on_submit, shareAtOldDiff) = Interfaces.template_registry.submit_share(job_id,
                worker_name, session, extranonce1_bin, extranonce2, ntime, nonce, difficulty)
        except SubmitException as e:
            # block_header and block_hash are None when submitted data are corrupted
            Interfaces.share_manager.on_submit_share(worker_name, False, False, difficulty,
                submit_time, False, ip, e[0], 0)    
            raise
            
        # Let's patch at least for a share that didn't met job.target (aka didn't solve a block)
        # shareAtOldDiff still at false : cool, nothing unusual
        if shareAtOldDiff = false:
                Interfaces.share_manager.on_submit_share(worker_name, block_header,
                block_hash, difficulty, submit_time, True, ip, '', share_diff)
        else:
                # This is a share that was above actual stratum diff but below old diff. 
                # The cool thing is this old diff is shareAtOldDiff.
                Interfaces.share_manager.on_submit_share(worker_name, block_header,
                block_hash, shareAtOldDiff, submit_time, True, ip, '', share_diff)
        
        if on_submit != None:
            # Pool performs submitblock() to litecoind. Let's hook
            # to result and report it to share manager
            on_submit.addCallback(Interfaces.share_manager.on_submit_block,
                worker_name, block_header, block_hash, submit_time, ip, share_diff)

        return True
            
    # Service documentation for remote discovery
    update_block.help_text = "Notify Stratum server about new block on the network."
    update_block.params = [('password', 'string', 'Administrator password'), ]
    
    authorize.help_text = "Authorize worker for submitting shares on this connection."
    authorize.params = [('worker_name', 'string', 'Name of the worker, usually in the form of user_login.worker_id.'),
                        ('worker_password', 'string', 'Worker password'), ]
    
    subscribe.help_text = "Subscribes current connection for receiving new mining jobs."
    subscribe.params = []
    
    submit.help_text = "Submit solved share back to the server. Excessive sending of invalid shares "\
                       "or shares above indicated target (see Stratum mining docs for set_target()) may lead "\
                       "to temporary or permanent ban of user,worker or IP address."
    submit.params = [('worker_name', 'string', 'Name of the worker, usually in the form of user_login.worker_id.'),
                     ('job_id', 'string', 'ID of job (received by mining.notify) which the current solution is based on.'),
                     ('extranonce2', 'string', 'hex-encoded big-endian extranonce2, length depends on extranonce2_size from mining.notify.'),
                     ('ntime', 'string', 'UNIX timestamp (32bit integer, big-endian, hex-encoded), must be >= ntime provided by mining,notify and <= current time'),
                     ('nonce', 'string', '32bit integer, hex-encoded, big-endian'), ]
        
