import json
import anthropic
from typing import Dict, Any, List
from langfuse import observe, get_client
from private_rag_apps.core.config import settings
from private_rag_apps.prompts.judge import JUDGE_FAITHFULNESS_PROMPT, JUDGE_ANSWER_RELEVANCE_PROMPT

def _call_judge(prompt: str) -> Dict[str, Any]:
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    try:
        response = client.messages.create(
            model=settings.judge_model,
            temperature=settings.judge_temperature,
            max_tokens=256,
            system="あなたはJSONを出力する評価アシスタントです。",
            messages=[{"role": "user", "content": prompt}]
        )
        
        # Record usage
        try:
            get_client().update_current_generation(
                usage_details={
                    "input": response.usage.input_tokens,
                    "output": response.usage.output_tokens
                },
                model=settings.judge_model
            )
        except Exception:
            pass

        text = getattr(response.content[0], "text", "").strip()
        
        # Extract JSON from potential markdown blocks
        if text.startswith("```json"):
            text = text.replace("```json", "").replace("```", "").strip()
        elif text.startswith("```"):
            text = text.replace("```", "").strip()

        return json.loads(text)
    except Exception as e:
        print(f"Judge error: {e}")
        return {"score": 0, "rationale": f"Error parsing judge output: {e}"}

@observe(as_type="generation", name="judge_faithfulness")
def evaluate_faithfulness(question: str, answer: str, context_chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
    context_text = "\n\n".join([f"[{i+1}] {chunk['content']}" for i, chunk in enumerate(context_chunks)])
    prompt = JUDGE_FAITHFULNESS_PROMPT.format(context=context_text, answer=answer)
    return _call_judge(prompt)

@observe(as_type="generation", name="judge_answer_relevance")
def evaluate_answer_relevance(question: str, answer: str, reference_answer: str) -> Dict[str, Any]:
    prompt = JUDGE_ANSWER_RELEVANCE_PROMPT.format(question=question, reference_answer=reference_answer, answer=answer)
    return _call_judge(prompt)
