"""Import REW generic EQ text, cut-only PK filters; reject unsupported lines."""
import argparse
import json
import re
from .profile import load, validate

PATTERN = re.compile(r'^Filter\s+\d+:\s+ON\s+PK\s+Fc\s+([\d.]+)\s+Hz\s+Gain\s+(-?[\d.]+)\s+dB\s+Q\s+([\d.]+)\s*$')

def parse(text):
    result=[]
    for line in text.splitlines():
        line=line.strip()
        if not line.startswith('Filter '):continue
        if ': OFF ' in line:continue
        match=PATTERN.fullmatch(line)
        if not match:raise ValueError('unsupported enabled REW filter; only ON PK accepted')
        f,g,q=map(float,match.groups());result.append({'freq':f,'gain':g,'q':q})
    if not result:raise ValueError('no enabled filters')
    return result

def main():
    a=argparse.ArgumentParser();a.add_argument('profile');a.add_argument('channel',choices=['left','right','sub']);a.add_argument('rew');a.add_argument('output');args=a.parse_args()
    p=load(args.profile)
    with open(args.rew) as f:p['channels'][args.channel]['peq']=parse(f.read())
    p=validate(p)
    with open(args.output,'x') as f:json.dump(p,f,indent=2)

if __name__=='__main__':main()
