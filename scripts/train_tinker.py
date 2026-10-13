"""Step 4: LoRA fine-tune Qwen3.5-4B on Tinker with the two pipeline tasks.

Usage: train_tinker.py <run_name> [epochs] [batch] [rank]
Writes runs/<run_name>/log.jsonl (train/dev loss per step) and runs/<run_name>/result.json (sampler path).
"""
import json, math, os, random, sys, time, pathlib
sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from db import env
os.environ.setdefault("TINKER_API_KEY", env("TINKER_API_KEY"))

import tinker
from tinker_cookbook import hyperparam_utils, model_info, renderers
from tinker_cookbook.renderers import TrainOnWhat
from tinker_cookbook.supervised.data import conversation_to_datum
from tinker_cookbook.supervised.common import compute_mean_nll
from tinker_cookbook.tokenizer_utils import get_tokenizer

BASE = "Qwen/Qwen3.5-4B"
run = sys.argv[1]
epochs = float(sys.argv[2]) if len(sys.argv) > 2 else 2
batch = int(sys.argv[3]) if len(sys.argv) > 3 else 64
rank = int(sys.argv[4]) if len(sys.argv) > 4 else 32
limit = int(os.environ.get("SFT_LIMIT", "0"))  # for a quick trial run

out = ROOT / "runs" / run
out.mkdir(parents=True, exist_ok=True)
tok = get_tokenizer(BASE)
name = model_info.get_recommended_renderer_name(BASE)
renderer = renderers.get_renderer(name + "_disable_thinking" if name == "qwen3_5" else name, tok)


def load(split):
    rows = [json.loads(l) for l in open(pathlib.Path(os.environ.get("SFT_DIR", ROOT / "data/sft")) / f"{split}.jsonl", encoding="utf-8")]
    return [conversation_to_datum(r["messages"], renderer, max_length=2048, train_on_what=TrainOnWhat.LAST_ASSISTANT_MESSAGE) for r in rows]


train, dev = load("train"), load("dev")
if limit:
    train = train[:limit]
random.seed(1)
dev_sample = random.sample(dev, min(128, len(dev)))
steps = math.ceil(len(train) * epochs / batch)
lr = hyperparam_utils.get_lr(BASE)
print(f"train={len(train)} dev={len(dev)} steps={steps} lr={lr:.2e} rank={rank}", flush=True)

sc = tinker.ServiceClient()
resume = out / "resume.json"  # last saved state, so a crash costs at most 100 steps
start = 0
if resume.exists():
    rs = json.load(open(resume))
    tc = sc.create_training_client_from_state_with_optimizer(rs["state_path"])
    start = rs["step"] + 1
    print("resuming from step", start, flush=True)
else:
    tc = sc.create_lora_training_client(base_model=BASE, rank=rank)
log = open(out / "log.jsonl", "a", encoding="utf-8")
random.seed(1000 + start)
order = []
t0 = time.time()
for step in range(start, steps):
    if len(order) < batch:
        idx = list(range(len(train)))
        random.shuffle(idx)
        order += idx
    data = [train[i] for i in order[:batch]]
    order = order[batch:]
    lr_now = lr * (1 - step / steps)  # linear decay
    fb = tc.forward_backward(data, loss_fn="cross_entropy")
    opt = tc.optim_step(tinker.AdamParams(learning_rate=lr_now, beta1=0.9, beta2=0.95, eps=1e-8))
    fbr = fb.result()
    opt.result()
    train_nll = compute_mean_nll([o["logprobs"] for o in fbr.loss_fn_outputs], [d.loss_fn_inputs["weights"] for d in data])
    rec = {"step": step, "lr": lr_now, "train_nll": train_nll, "elapsed_s": round(time.time() - t0)}
    if step % 20 == 0 or step == steps - 1:
        ev = tc.forward(dev_sample, loss_fn="cross_entropy").result()
        rec["dev_nll"] = compute_mean_nll([o["logprobs"] for o in ev.loss_fn_outputs], [d.loss_fn_inputs["weights"] for d in dev_sample])
    if step and step % 100 == 0:
        rec["checkpoint"] = tc.save_weights_for_sampler(name=f"{run}-s{step}").result().path
        st = tc.save_state(name=f"{run}-state-s{step}").result().path
        json.dump({"step": step, "state_path": st}, open(resume, "w"))
    log.write(json.dumps(rec) + "\n"); log.flush()
    print(rec, flush=True)

path = tc.save_weights_for_sampler(name=f"{run}-final").result().path
state = tc.save_state(name=f"{run}-state").result().path
json.dump({"run": run, "base": BASE, "rank": rank, "epochs": epochs, "batch": batch, "steps": steps, "lr": lr,
           "train_examples": len(train), "sampler_path": path, "state_path": state, "minutes": round((time.time() - t0) / 60, 1)},
          open(out / "result.json", "w"), indent=2)
print("sampler path:", path, flush=True)
