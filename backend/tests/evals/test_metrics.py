import math
from private_rag_apps.evals.metrics import evaluate_retrieval
from private_rag_apps.evals.schema import RelevantDoc

def test_evaluate_retrieval_doc_dedup():
    expected_docs = [
        RelevantDoc(path="doc1.md", grade=1),
        RelevantDoc(path="doc2.md", grade=1)
    ]
    
    retrieved_chunks = [
        {"path": "doc3.md"}, # Irrelevant
        {"path": "doc1.md"}, # Hit 1 (rank 2)
        {"path": "doc1.md"}, # Ignored (doc-dedup)
        {"path": "doc2.md"}, # Hit 2 (rank 4)
    ]
    
    metrics = evaluate_retrieval(retrieved_chunks, expected_docs)
    
    # rank 1: 0
    # rank 2: 1
    # rank 3: 0 (dedup)
    # rank 4: 1
    
    # Recall@5 = 1.0 (since doc1 is in top 5, doc2 is in top 5)
    assert metrics["recall_5"] == 1.0
    
    # nDCG@10: 
    # DCG = 1/log2(2+1) + 1/log2(4+1) = 1/log2(3) + 1/log2(5) = 1/1.5849 + 1/2.3219 = 0.6309 + 0.4306 = 1.0615
    # IDCG = 1/log2(1+1) + 1/log2(2+1) = 1/1 + 1/1.5849 = 1.6309
    # nDCG = 1.0615 / 1.6309 ≈ 0.6508
    expected_dcg = (1 / math.log2(3)) + (1 / math.log2(5))
    expected_idcg = 1.0 + (1 / math.log2(3))
    assert math.isclose(metrics["ndcg_10"], expected_dcg / expected_idcg, rel_tol=1e-5)
    
    # MRR: first hit is at rank 2 (index 1), so 1/2 = 0.5
    assert metrics["mrr"] == 0.5


def test_evaluate_retrieval_negative():
    expected_docs = [] # negative case
    retrieved_chunks = [{"path": "doc1.md"}]
    
    metrics = evaluate_retrieval(retrieved_chunks, expected_docs)
    assert metrics["recall_5"] == 1.0
    assert metrics["recall_10"] == 1.0
    assert metrics["ndcg_10"] == 1.0
    assert metrics["mrr"] == 1.0
