"""Atomic bounded journal for the organization Runtime connector."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from .base import RepositoryBase, StoreError
class OrganizationConnectorRepository:
 def __init__(self,base:RepositoryBase):self.base=base;self.root=base.data_dir/'organization-connector';self.root.mkdir(parents=True,exist_ok=True,mode=0o700);self.state_path=self.root/'state.json';self.inbox_path=self.root/'inbox.json';self.outbox_path=self.root/'outbox.json'
 def _read(self,path:Path,default:Any):
  if not path.exists():return default
  value=self.base._read_json(path) if hasattr(self.base,'_read_json') else __import__('json').loads(path.read_text())
  return value
 def state(self)->dict[str,Any]:return dict(self._read(self.state_path,{}))
 def save_state(self,value:dict[str,Any])->None:self.base.atomic_json(self.state_path,value);self.state_path.chmod(0o600)
 def seen(self,command_id:str)->bool:return command_id in self._read(self.inbox_path,{})
 def record(self,command_id:str,digest:str,state:str)->None:
  items=dict(self._read(self.inbox_path,{}));existing=items.get(command_id)
  if existing and existing.get('digest')!=digest:raise StoreError('command replay digest mismatch',status=409,code='command_replay')
  items[command_id]={'digest':digest,'state':state};items=dict(list(items.items())[-1000:]);self.base.atomic_json(self.inbox_path,items)
 def enqueue(self,item:dict[str,Any])->None:
  rows=list(self._read(self.outbox_path,[]));key=item['idempotency_key'];rows=[r for r in rows if r.get('idempotency_key')!=key];rows.append(item)
  if len(rows)>500:raise StoreError('connector outbox is full',status=503,code='connector_outbox_full')
  self.base.atomic_json(self.outbox_path,rows)
 def outbox(self)->list[dict[str,Any]]:return list(self._read(self.outbox_path,[]))
 def complete(self,key:str)->None:self.base.atomic_json(self.outbox_path,[r for r in self.outbox() if r.get('idempotency_key')!=key])
