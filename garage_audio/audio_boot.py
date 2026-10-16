"""Root-operated NETWORK lifecycle transaction; no grants or provider enrollment.

Consumes frozen Compose inputs. Never called from HTTP/MCP; exposes no socket.
All stateful roles are created and registered before start; app execution waits for
its private route. Any lifetime loss closes the worker and requires fresh containers.
"""
import hashlib,json,os,signal,stat,subprocess,time
from pathlib import Path
from .audio_network import Carrier,atomic,docker,run
from .audio_device import device_environment

CONFIG=Path('/etc/garage-audio/audio-boot.json')
ROLES=('audio-device','audio-transport','music-assistant','audio-observer','dashboard')

def frozen_file(item):
    path=Path(item['path'])
    if not path.is_absolute():raise PermissionError('absolute frozen input path required')
    for part in (path,*path.parents):
        info=part.lstat()
        if part.is_symlink() or info.st_uid!=0 or info.st_mode&0o022:raise PermissionError('root immutable package input required')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise PermissionError('frozen input changed')
    return path

def load_config():
    info=CONFIG.lstat()
    if os.geteuid()!=0 or not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o077:raise PermissionError('private root boot configuration required')
    config=json.loads(CONFIG.read_text())
    if set(config)!={'project','compose','environment','images','hook','ma_settings','receiver_binary_sha256'} or config['project']!='garage-audio-next':raise ValueError('exact network-only boot schema required')
    if len(config['receiver_binary_sha256'])!=64 or any(c not in '0123456789abcdef' for c in config['receiver_binary_sha256']):raise ValueError('authorized actual receiver binary digest required')
    if config['ma_settings']!='/home/zach/garage-audio/state/ma/settings.json':raise PermissionError('existing retained MA data only')
    if set(config['images'])!=set(ROLES):raise ValueError('exact frozen role images required')
    for image in config['images'].values():
        if not image.startswith('sha256:') or len(image)!=71:raise ValueError('immutable image ID required')
        if docker('image','inspect','--format','{{.Id}}',image).strip()!=image:raise PermissionError('local exact image unavailable; no pull fallback')
    config['compose']=[str(frozen_file(x)) for x in config['compose']]
    config['environment']=str(frozen_file(config['environment']))
    config['hook']=str(frozen_file(config['hook']))
    return config

class Boot:
    def __init__(self,carrier,config):
        self.carrier=carrier;self.config=config;self.roles={}
    def compose(self,*args):
        command=['docker','compose','--project-name',self.config['project'],'--env-file',self.config['environment'],
                 '--env-file',str(self.carrier.receipt/'generation.env')]
        for filename in self.config['compose']:command+=['-f',filename]
        return subprocess.check_output([*command,*args],text=True,timeout=45)
    def create(self):
        c=self.carrier;c.healthy();network=c.management_network()
        env={'GARAGE_AUDIO_CARRIER_ID':c.id,'GARAGE_AUDIO_NETWORK_GENERATION':c.generation,
             'GARAGE_AUDIO_NETWORK_AUTHORITY_DIR':str(c.receipt/'authority'),
             'GARAGE_AUDIO_LAN_LEASE':c.address,'GARAGE_AUDIO_MANAGEMENT_NETWORK':network}
        env['GARAGE_SOLOIST_RUNTIME']='/network/receiver'
        (c.receipt/'generation.env').write_text(''.join(k+'='+v+'\n' for k,v in env.items()))
        plan=json.loads(self.compose('config','--format','json'))
        worker=plan['services']['audio-device']
        command=worker.get('command',[])
        hardware=isinstance(command,list) and '--mode' in command and command[command.index('--mode')+1:] == ['hardware']
        expected_devices=None
        if hardware:
            # Frozen configuration supplies policy/images, never a persistent ALSA index.
            # Resolve metadata only; wrong ownership/identity fails before create or settings writes.
            env.update(device_environment())
            (c.receipt/'generation.env').write_text(''.join(k+'='+v+'\n' for k,v in env.items()))
            plan=json.loads(self.compose('config','--format','json'))
            worker=plan['services']['audio-device']
            pcm=env['GARAGE_AUDIO_PCM_NODE'];control=env['GARAGE_AUDIO_CONTROL_NODE']
            expected_devices=[{'source':pcm,'target':pcm,'permissions':'rw'},
                              {'source':control,'target':control,'permissions':'rw'}]
            read_control=[expected_devices[0],dict(expected_devices[1],permissions='r')]
            if worker.get('devices') not in (expected_devices,read_control) or worker.get('privileged') or worker.get('cap_add') or worker.get('device_cgroup_rules'):
                raise PermissionError('exact identity-resolved UMC mapping and least privilege required')
            expected_devices=worker['devices']
        elif worker.get('devices'):
            raise PermissionError('sound devices require explicit hardware mode')
        for role in ROLES:
            value=plan['services'][role]
            if value['image']!=self.config['images'][role] or value.get('restart')!='no':raise PermissionError('role image/restart differs from frozen plan')
            if value.get('labels',{}).get('garage.audio.generation')!=c.generation:raise PermissionError('exact generation role required')
        if self.config.get('ma_settings'):
            self.network_settings()
        # `create` never starts dependency processes; all IDs become guardian-owned first.
        self.compose('create','--no-build','--pull','never','audio-volume-init',*ROLES)
        for role in ('audio-volume-init',*ROLES):
            identities=self.compose('ps','--all','--quiet',role).split()
            if len(identities)!=1:raise PermissionError('one exact created role required')
            identity=json.loads(docker('inspect',identities[0]))[0]['Id'];self.roles[role]=identity
            value=json.loads(docker('inspect',identity))[0]
            ack=None
            if role=='audio-device':
                if expected_devices is not None:
                    actual=value['HostConfig']
                    devices=[{'source':d['PathOnHost'],'target':d['PathInContainer'],'permissions':d['CgroupPermissions']} for d in actual.get('Devices',[])]
                    if devices!=expected_devices or actual.get('Privileged') or actual.get('CapAdd') or actual.get('DeviceCgroupRules'):
                        raise PermissionError('created worker differs from identity-resolved UMC mapping')
                mounts=[m for m in value['Mounts'] if m['Destination']=='/evidence']
                if len(mounts)!=1:raise PermissionError('worker acknowledgement mount required')
                ack=Path(mounts[0]['Source'])/'network-closed.json'
            c.register(identity,worker_ack=ack)
        atomic(c.receipt/'boot-inputs.json',{'roles':self.roles,'images':self.config['images'],'generation':c.generation})
        return network
    def network_settings(self):
        """Change only supported bind/publish network settings, never account/provider state."""
        path=Path(self.config['ma_settings'])
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size>2*1024*1024:
            raise PermissionError('bounded retained MA configuration required')
        identities=docker('ps','-q').split()
        if identities:
            for item in json.loads(docker('inspect',*identities)):
                if any(m.get('Source')==str(path.parent) for m in item['Mounts']):
                    raise PermissionError('retained MA must be quiesced by cutover owner before network settings')
        raw=path.read_bytes();value=json.loads(raw)
        backup=self.carrier.receipt/'retained-settings-before.json'
        with backup.open('xb') as f:f.write(raw)
        backup.chmod(0o600)
        core=value.setdefault('core',{})
        for domain in ('streams','webserver'):
            section=core.setdefault(domain,{'domain':domain,'values':{}})
            section.setdefault('values',{}).update(bind_ip='0.0.0.0',publish_ip=self.carrier.address)
        temporary=path.with_name(path.name+'.network-'+self.carrier.generation)
        with temporary.open('xb') as f:f.write(json.dumps(value).encode());f.flush();os.fsync(f.fileno())
        os.chown(temporary,info.st_uid,info.st_gid);os.chmod(temporary,stat.S_IMODE(info.st_mode))
        os.replace(temporary,path)
    def start(self,network):
        c=self.carrier
        # Volume initialization is a finite metadata/ownership job, never an audio process.
        docker('start','-a',self.roles['audio-volume-init'])
        if json.loads(docker('inspect',self.roles['audio-volume-init']))[0]['State']['ExitCode']!=0:raise RuntimeError('volume setup failed')
        for role in ROLES:
            c.start_registered(self.roles[role],shared_namespace=role in ('audio-transport','music-assistant','audio-observer'))
            if role=='dashboard':
                # Entry waits on app-ready; no app request can race these host routes.
                c.management(network,self.roles[role])
                pid=json.loads(docker('inspect',self.roles[role]))[0]['State']['Pid']
                run('nsenter','-t',str(pid),'-n','ip','route','add',c.address+'/32','dev','eth0')
                c.healthy()
                atomic(c.receipt/'authority/app-ready.json',{'generation':c.generation,'route_ready':True},mode=0o444)
        atomic(c.receipt/'boot-ready.json',{'generation':c.generation,'roles':self.roles,'sound_authority':False})

def main():
    config=load_config();stop=False
    def stopping(*_):
        nonlocal stop
        stop=True
    signal.signal(signal.SIGTERM,stopping);signal.signal(signal.SIGINT,stopping)
    root=Path('/var/lib/garage-audio-network');root.mkdir(mode=0o700,exist_ok=True)
    receipt=root/('generation-'+str(time.time_ns()))
    c=Carrier(receipt)
    try:
        c.start(config['hook']);transaction=Boot(c,config)
        network=transaction.create();transaction.start(network)
        from .audio_receiver import ReceiverEndpoint,advertised_pairing,pairing_instances,verify_pairing_instances
        receiver=ReceiverEndpoint(config['receiver_binary_sha256']);observed=None;deadline=time.monotonic()+60
        pairing=[];pairing_check=0;pairing_baseline=[]
        while not stop:
            c.healthy()
            pid=json.loads(docker('inspect',transaction.roles['music-assistant']))[0]['State']['Pid']
            try:current=receiver.observe(pid,c.inode)
            except (OSError,ValueError,PermissionError):
                if observed is not None or time.monotonic()>deadline:raise RuntimeError('actual receiver listener ownership unavailable')
            else:
                if observed is not None and current!=observed:raise RuntimeError('receiver listener generation changed')
                receiver.publish(c.receipt/'authority',current);observed=current
                c.receiver_process={'pid':current['pid'],'birth':current['birth']};c.write_registry()
                atomic(c.receipt/'receiver-listener.json',current)
                if pairing_baseline:verify_pairing_instances(current,pid,c.inode,c.address,pairing_baseline)
                if time.monotonic()-pairing_check>3:
                    instances=pairing_instances(current,pid,c.inode,c.address,advertised_pairing(transaction.roles['music-assistant'],c.address))
                    ports=sorted({r['port'] for r in instances})
                    if instances and pairing_baseline and instances!=pairing_baseline:raise RuntimeError('pairing LISTEN instance changed')
                    if pairing and ports!=pairing:raise RuntimeError('qualified pairing listener changed')
                    if not pairing and ports:
                        c.ns('nft','add','element','inet','garage_audio','pairing','{',','.join(map(str,ports)),'}')
                        pairing=ports
                        pairing_baseline=instances
                        atomic(c.receipt/'pairing-listeners.json',{'generation':c.generation,'ports':ports,'listener_instances':instances,'advertisement':'_spotify-connect._tcp.local.','receiver':current})
                    pairing_check=time.monotonic()
            time.sleep(.2)
    finally:c.close()

if __name__=='__main__':main()
