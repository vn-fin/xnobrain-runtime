import hashlib, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from xnobrain.services.organization_artifacts import OrganizationArtifactsServiceMixin
from xnobrain.services.base import ServiceError
class Agents:
 def __init__(self,root):self.root=Path(root)
 def workspace_dir(self,_):return self.root
 _workspace_dir=workspace_dir
 def _require_profile(self,_):pass
 def _agent_name(self,x):return x
 def _workspace_path(self,_,value,require_file=True):
  p=(self.root/str(value)).resolve()
  if self.root.resolve() not in p.parents and p!=self.root.resolve():raise ValueError
  return p
class Checkpoints:
 def mutation(self,*_):
  from contextlib import nullcontext
  return nullcontext()
class Service(OrganizationArtifactsServiceMixin):
 def __init__(self,root):self.agents=Agents(root);self.checkpoints=Checkpoints()
 workspace_file=lambda self,a,p:self.agents._workspace_path(a,p)
class Response:
 status=200
 def __init__(self,data=b''):self.data=data
 def read(self,n=-1):x=self.data[:n];self.data=self.data[n:];return x
 def __enter__(self):return self
 def __exit__(self,*_):pass
class TestArtifacts(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.s=Service(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def test_inspect_rejects_traversal_and_hashes(self):
  Path(self.tmp.name,'a.txt').write_bytes(b'abc');out=self.s.inspect_organization_artifact('a',{'path':'a.txt'});self.assertEqual(out['sha256'],hashlib.sha256(b'abc').hexdigest())
  with self.assertRaises(Exception):self.s.inspect_organization_artifact('a',{'path':'../x'})
 def test_publish_requires_explicit_approval(self):
  Path(self.tmp.name,'a').write_bytes(b'x')
  with self.assertRaises(ServiceError):self.s.publish_organization_artifact('a',{'path':'a','approved':False})
 def test_import_is_atomic_and_checksum_verified(self):
  data=b'payload';body={'file_id':'f','version_id':'v','name':'x','size_bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'media_type':'text/plain','destination':'runs/input.txt','collision':'cancel','transfer':{'url':'https://store/get','method':'GET','expires_at':'2999-01-01T00:00:00Z'}}
  with patch('xnobrain.services.organization_artifacts._open',return_value=Response(data)):out=self.s.import_organization_artifact('a',body)
  self.assertEqual(Path(self.tmp.name,out['path']).read_bytes(),data)
  self.assertFalse(list(Path(self.tmp.name,'runs').glob('.org-import-*')))
 def test_import_rejects_protected_and_mismatch(self):
  body={'file_id':'f','version_id':'v','name':'x','size_bytes':1,'sha256':hashlib.sha256(b'x').hexdigest(),'media_type':'x','destination':'skills/x','collision':'cancel','transfer':{'url':'https://store/get','method':'GET','expires_at':'2999-01-01T00:00:00Z'}}
  with self.assertRaises(ServiceError):self.s.import_organization_artifact('a',body)
