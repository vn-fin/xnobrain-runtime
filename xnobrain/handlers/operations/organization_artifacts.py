from typing import Any,Callable
Operation=tuple[Callable[[],Any],str,int]
def operations(handler:Any,request:Any,body:dict[str,Any])->dict[str,Operation]:
 p,s=request.path_params,handler.service
 return {"organization_artifact_inspect":(lambda:s.inspect_organization_artifact(p["agent_id"],body),"local artifact inspected",200),"organization_artifact_publish":(lambda:s.publish_organization_artifact(p["agent_id"],body),"artifact published",200),"organization_artifact_import":(lambda:s.import_organization_artifact(p["agent_id"],body),"artifact imported",201)}
