"""Public dependency/binary fingerprint for audited cached derivative; not an apt lock."""
import hashlib,importlib.metadata,json,pathlib,subprocess,sys
paths=['/opt/garage-audio/dsp/camilladsp','/usr/bin/pipewire','/usr/bin/wireplumber','/usr/bin/squeezelite','/usr/bin/dbus-daemon',sys.executable]
paths+=[str(p) for p in pathlib.Path('/opt/garage-audio/dsp/lib').rglob('*') if p.is_file()]
result={'schema':1,'kind':'audited-cached-runtime-fingerprint-not-clean-install-apt-lock',
 'base_image':'sha256:ece60bd70b5dbfe21d20254988c25485d9b1229d24c44955e9ab52b663865d7a',
 'binaries':{p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in sorted(set(paths))},
 'dpkg':subprocess.check_output(['dpkg-query','-W','-f=${Package}\t${Version}\t${Architecture}\n'],text=True).splitlines(),
 'python':sorted([[p.metadata['Name'],p.version] for p in importlib.metadata.distributions()])}
print(json.dumps(result,sort_keys=True,indent=2))
