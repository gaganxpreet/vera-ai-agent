from typing import List, Optional

def detect_language_preference(
    merchant_langs: Optional[List[str]] = None,
    customer_lang_pref: Optional[str] = None,
    message_text: Optional[str] = None
) -> str:
    """
    Returns one of: 'en', 'hi-en', 'hi'
    """
    # 1. Customer explicit preference takes top priority for customer context
    if customer_lang_pref:
        pref = customer_lang_pref.lower().strip()
        if "hi-en" in pref or "mix" in pref or "hinglish" in pref:
            return "hi-en"
        if "hi" in pref:
            return "hi"
        if "en" in pref:
            return "en"

    # 2. Check if inbound message has Hindi / Hinglish keywords
    if message_text:
        text = message_text.lower()
        hinglish_words = ["aap", "apke", "karo", "bhai", "shukriya", "hai", "nahi", "accha", "theek", "batao", "kripya", "namaste"]
        if any(w in text for w in hinglish_words):
            return "hi-en"

    # 3. Merchant languages list
    if merchant_langs:
        langs = [l.lower() for l in merchant_langs]
        if "hi" in langs and "en" in langs:
            return "hi-en"
        if "hi" in langs:
            return "hi"
            
    return "en"
