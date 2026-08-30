"""Safe installation of Control-approved visible marketplace definitions."""
from __future__ import annotations
import hashlib,json,os,re,tempfile
from pathlib import Path
from typing import Any,Mapping
from .base import ServiceError
_SAFE=re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
class MarketplaceService:
 def __init__(self,repository,agents):self.repository=repository;self.agents=agents
 @staticmethod
 def digest(package:Mapping[str,Any])->str:
  value={'definition':package['definition'],'permissions':package.get('requested_permissions',[]),'compatibility':package.get('compatibility',{}),'license':package.get('license','')};payload=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode();return 'sha256:'+hashlib.sha256(payload).hexdigest()
 def install(self,package:Mapping[str,Any])->dict[str,Any]:
  definition=dict(package.get('definition') or {});expected=str(package.get('digest') or '')
  if self.digest(package)!=expected:raise ServiceError('marketplace package digest mismatch',status=422,code='package_digest_mismatch')
  if package.get('status') not in {'pending','installed'}:raise ServiceError('installation is unavailable',status=409,code='installation_unavailable')
  soul=str(definition.get('soul') or '');skills=dict(definition.get('skills') or {});assets=dict(definition.get('assets') or {})
  if not soul or any(not _SAFE.fullmatch(str(k)) or len(str(v))>1_000_000 for k,v in {**skills,**assets}.items()):raise ServiceError('marketplace package is unsafe',status=422,code='package_rejected')
  profile_id='market-'+str(package['id']).replace('inst_','')[:24];final=self.repository.profile_path(profile_id)
  if final.exists():raise ServiceError('installation profile already exists',status=409,code='installation_exists')
  stage=Path(tempfile.mkdtemp(prefix='.market-',dir=self.repository.profiles_root));installed=False
  try:
   (stage/'workspace').mkdir();(stage/'skills'/'custom').mkdir(parents=True);(stage/'SOUL.md').write_text(soul,encoding='utf-8')
   public=dict(definition.get('public_config') or {});allowed={'model','reasoning','display_name','description'};config={k:v for k,v in public.items() if k in allowed};config['xnobrain']={'marketplace_installation_id':package['id'],'package_digest':expected,'update_policy':package.get('update_policy','pinned')};self.repository.atomic_yaml(stage/'config.yaml',config)
   for name,content in skills.items():d=stage/'skills'/'custom'/name;d.mkdir();(d/'SKILL.md').write_text(str(content),encoding='utf-8')
   for name,content in assets.items():d=stage/'workspace'/'assets';d.mkdir(exist_ok=True);(d/name).write_text(str(content),encoding='utf-8')
   # Mutable customer state starts empty and is never sourced from publisher bytes.
   (stage/'memories').mkdir();os.replace(stage,final);installed=True
  finally:
   if not installed and stage.exists():__import__('shutil').rmtree(stage)
  self.agents.sync_profiles_registry();return {'installation_id':package['id'],'local_profile_id':profile_id,'digest':expected,'ownership':'customer','execution_mode':'package_visible'}
 def update(self,package:Mapping[str,Any],local_profile_id:str)->dict[str,Any]:
  profile=self.repository.profile_path(local_profile_id)
  if not profile.is_dir():raise ServiceError('installation profile not found',status=404,code='not_found')
  old_config=self.repository._read_yaml(profile/'config.yaml') or {};memory=(profile/'memories');workspace=(profile/'workspace');snapshots={p.relative_to(profile):p.read_bytes() for root in (memory,workspace) if root.exists() for p in root.rglob('*') if p.is_file() and not p.is_symlink()}
  if self.digest(package)!=package.get('digest'):raise ServiceError('marketplace package digest mismatch',status=422,code='package_digest_mismatch')
  definition=dict(package['definition']);(profile/'SOUL.md').write_text(str(definition['soul']),encoding='utf-8');public={k:v for k,v in dict(definition.get('public_config') or {}).items() if k in {'model','reasoning','display_name','description'}};public['xnobrain']=dict(old_config.get('xnobrain') or {});public['xnobrain']['package_digest']=package['digest'];self.repository.atomic_yaml(profile/'config.yaml',public)
  for relative,payload in snapshots.items():target=profile/relative;target.parent.mkdir(parents=True,exist_ok=True);self.repository.atomic_write(target,payload)
  return {'installation_id':package['id'],'local_profile_id':local_profile_id,'digest':package['digest'],'updated':True}
 def uninstall(self,local_profile_id:str)->dict[str,Any]:
  target=self.repository.soft_delete_profile(local_profile_id);self.agents.sync_profiles_registry();return {'local_profile_id':local_profile_id,'status':'uninstalled','recoverable_path':target.name}
