import json
import openai
from typing import Dict, Any, List
from langfuse import observe, get_client
from private_rag_apps.core.config import settings
from private_rag_apps.prompts.judge import (
    JUDGE_FAITHFULNESS_PROMPT,
    JUDGE_ANSWER_RELEVANCE_PROMPT,
    JUDGE_DIRECT_GROUNDEDNESS_PROMPT,
    JUDGE_SUPPLEMENT_FORMAT_PROMPT,
)


def _call_judge(prompt: str) -> Dict[str, Any]:
    client = openai.OpenAI(api_key=settings.openai_api_key)
    try:
        response = client.responses.create(
            model=settings.judge_model,
            temperature=settings.judge_temperature,
            max_output_tokens=256,
            instructions="あなたはJSONを出力する評価アシスタントです。",
            input=prompt,
        )

        # Record usage
        try:
            if response.usage is not None:
                get_client().update_current_generation(
                    usage_details={
                        "input": response.usage.input_tokens,
                        "output": response.usage.output_tokens,
                    },
                    model=settings.judge_model,
                )
        except Exception:
            pass

        text = response.output_text.strip()

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
def evaluate_faithfulness(
    question: str, answer: str, context_chunks: List[Dict[str, Any]]
) -> Dict[str, Any]:
    context_text = "\n\n".join(
        [f"[{i + 1}] {chunk['content']}" for i, chunk in enumerate(context_chunks)]
    )
    prompt = JUDGE_FAITHFULNESS_PROMPT.format(context=context_text, answer=answer)
    return _call_judge(prompt)


@observe(as_type="generation", name="judge_answer_relevance")
def evaluate_answer_relevance(question: str, answer: str, reference_answer: str) -> Dict[str, Any]:
    prompt = JUDGE_ANSWER_RELEVANCE_PROMPT.format(
        question=question, reference_answer=reference_answer, answer=answer
    )
    return _call_judge(prompt)


@observe(as_type="generation", name="judge_direct_groundedness")
def evaluate_direct_groundedness(question: str, answer: str) -> Dict[str, Any]:
    """direct経路の回答がコーパス固有の固有名詞・数値を捏造していないかを評価する
    （スペック rev.3 §7.2 direct groundedness）。judgeが違反(score=0)と判定した件は
    人手裁定し、真の違反のみをカウントする運用とする（backend/evals/direct_groundedness_eval.py
    のdocstring参照。judgeの偽陽性のみでブロックしない）"""
    prompt = JUDGE_DIRECT_GROUNDEDNESS_PROMPT.format(question=question, answer=answer)
    return _call_judge(prompt)


@observe(as_type="generation", name="judge_supplement_format")
def evaluate_supplement_format(question: str, answer: str) -> Dict[str, Any]:
    """複合質問(コーパスで答えられる部分+一般知識のみで答えられる部分)への回答が、
    「一般知識に基づく補足」書式(区切り線+分離セクション+引用マーカー不使用)を
    守れているかを評価する（スペック rev.3 §7.2 補足書式の遵守）"""
    prompt = JUDGE_SUPPLEMENT_FORMAT_PROMPT.format(question=question, answer=answer)
    return _call_judge(prompt)
