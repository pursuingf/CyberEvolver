import sys
sys.path.append("../../")
from cudaLLM.tasks.alphaevolve_ac2.ae_seq import height_sequence_2
import json

def ttt_best():
    sequence = json.load(open("ttt_ac2_sequence.json"))["sequence"]
    return sequence

if __name__ == "__main__":
    from ThetaEvolveResults.SecondAutoCorrIneq.verify import verify_v2
    data = json.load(open("../../ThetaEvolveResults/SecondAutoCorrIneq/data.json"))
    for item in data:
        print(f"Result for {item['name']}")
        numbers = item['list']
        C_lower_bound = verify_v2(numbers)
        print(f"C_lower_bound: {C_lower_bound}")
    print(f"C_lower_bound: {verify_v2(height_sequence_2)}")
    
    ttt_sequence = ttt_best()
    print(f"TTT sequence length: {len(ttt_sequence)}")
    print(f"TTT sequence C_lower_bound: {verify_v2(ttt_sequence)}")
