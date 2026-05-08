from tabnanny import check
import yaml
import json
from pathlib import Path

cnt = 0

# 1. Define a custom Dumper class.
class IndentDumper(yaml.Dumper):
    def increase_indent(self, flow=False, indentless=False):
        # Force indentless=False so list items are indented.
        return super(IndentDumper, self).increase_indent(flow, False)
def process_json_files_pathlib(directory_path:str) -> dict:
    target_dir = Path(directory_path)
    
    challenges = {}
    for json_file in target_dir.glob("*.json"):
        if json_file.is_file():
            with open(json_file,'r') as f:
                challenges.update(json.load(f))
            
    return challenges

def check_docker_compose(dir:str) -> dict:
    flag = True
    file = Path(dir + "/docker-compose.runtime.yml")
    if not file.exists():
        return True
    with open(file,'r') as f:
        obj = yaml.safe_load(f)
    services:dict = obj["services"] 
    #networks:dict = obj["networks"]
    for key,value in services.items():
        assert isinstance(value,dict)
        challenge = Path(dir +"/challenge.json")
        if value.get("ports",None) == None and value.get("port",None) == None:
            flag = False
            if not challenge.exists():
                return False
            with open(challenge,'r') as f:
                challenge_obj = json.load(f)
            if challenge_obj.get("internal_port",None):
                port = challenge_obj["internal_port"]
                value["ports"] = [f"{port}:{port}"]
                obj["services"][key] = value
            elif challenge_obj.get("port",None):
                port = challenge_obj["port"]
                value["ports"] = [f"{port}:{port}"]
                obj["services"][key] = value
            else:
                return False
    #obj["networks"] = networks
    if not flag:
        print(f"Updating {dir} : docker-compose.yml")
        with open(file,'w') as f:
            yaml.dump(
                obj,
                f,
                Dumper=IndentDumper,
                sort_keys=False,    
                default_flow_style=False,  
                indent=2,            
            )

    return True

if __name__ == "__main__":
    challenges = process_json_files_pathlib(".")
    #print(challenges)
    for key,value in challenges.items():
        #print(value['path'])
    
        if not check_docker_compose(value['path']):            
            print(f"[MISS] {key}:{value['path']}/docker-compose.yml")
        if cnt >= 1 :
            break
