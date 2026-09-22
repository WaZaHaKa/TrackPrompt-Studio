"""Run synthetic/local tests only. No SSH, model requests, ComfyUI, or GPU rendering."""
import argparse,json,os,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--report',type=Path);a=p.parse_args()
root=Path(__file__).resolve().parents[1]
suites=[('master',root/'master/tests')]
for name in ('companion','trackprompt'):
    if (root/name/'tests').exists():suites.append((name,root/name/'tests'))
if (root/'packaging_tests').exists():suites.append(('packaging',root/'packaging_tests'))
env=dict(os.environ);env['PYTHONDONTWRITEBYTECODE']='1';env['PYTHONPATH']=os.pathsep.join(str(x) for x in [root/'master/src',root/'companion/src',root/'trackprompt/src',root/'tools'])
results=[]
for name,path in suites:
    r=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s',str(path),'-v'],env=env,text=True,capture_output=True,timeout=120)
    print(r.stdout,end='');print(r.stderr,end='')
    results.append({'suite':name,'exit_code':r.returncode,'passed':r.returncode==0,'output':r.stdout+r.stderr})
report={'runtime':sys.version,'platform':sys.platform,'suites':results,'all_passed':all(x['passed'] for x in results),'scope':'Local/synthetic; not Windows/WSL services, installed agents, existing app UI, or real render acceptance.'}
if a.report:
    a.report.parent.mkdir(parents=True,exist_ok=True)
    with a.report.open('x',encoding='utf-8') as f:json.dump(report,f,indent=2)
print(json.dumps({k:v for k,v in report.items() if k!='suites'},indent=2))
raise SystemExit(0 if report['all_passed'] else 1)
