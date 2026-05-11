from llama_cpp import Llama

MODEL_PATH = r"C:\Users\Belen Izetta\Documents\trae_projects\IA\models\qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"

llm = Llama(
    model_path=MODEL_PATH,
    n_ctx=2048,
    n_threads=4,   # podés subir si tu CPU lo permite
    verbose=True
)
*
prompt = """You are a senior software engineer.
Return ONLY the word OK if you understand this message.
"""

out = llm.create_chat_completion(
    messages=[
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": prompt},
    ],
    temperature=0.1,
)

print(out["choices"][0]["message"]["content"])
