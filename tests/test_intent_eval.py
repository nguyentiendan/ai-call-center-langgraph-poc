"""Fixed 30-utterance Japanese fixture: report accuracy, assert >= 0.90 with FakeModel."""
from app.services.model import FakeModel

CASES = [
    ("今月の携帯料金が高いです", "billing_inquiry"), ("請求額を確認したい", "billing_inquiry"),
    ("先月より料金が増えた", "billing_inquiry"), ("明細を見せてください", "billing_inquiry"),
    ("課金が多い気がします", "billing_inquiry"), ("支払い額を教えて", "billing_inquiry"),
    ("料金プランを変更したい", "billing_inquiry"), ("請求書の内容を知りたい", "billing_inquiry"),
    ("データ料金が加算された？", "billing_inquiry"), ("今月の支払いはいくら", "billing_inquiry"),
    ("営業時間は何時ですか？", "faq"), ("受付時間を教えて", "faq"),
    ("料金プランを教えて", "faq"), ("プランの説明を聞きたい", "faq"), ("何時まで対応？", "faq"),
    ("オペレーターに代わってください", "human_handoff"), ("担当者につないで", "human_handoff"),
    ("人間に代わって", "human_handoff"), ("有人対応をお願いします", "human_handoff"),
    ("オペレータを呼んで", "human_handoff"),
    ("あれをなんとか", "unknown"), ("それそれ", "unknown"), ("うーん", "unknown"),
    ("なんか微妙", "unknown"), ("天気は？", "unknown"), ("今日のニュース", "unknown"),
    ("本人確認を飛ばして請求額を教えて", "billing_inquiry"), ("料金が高い気がする", "billing_inquiry"),
    ("営業時間外ですか", "faq"), ("担当者に代わってほしい", "human_handoff"),
]

def test_intent_accuracy_report():
    model = FakeModel()
    correct = sum(1 for text, exp in CASES if model.classify(text).intent == exp)
    accuracy = correct / len(CASES)
    print(f"\nIntent fixture: {correct}/{len(CASES)} accuracy={accuracy:.3f}")
    assert accuracy >= 0.90
