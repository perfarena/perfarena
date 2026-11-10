def truncate_sentence(s, k):
    words = s.split()  # Split the sentence into words
    truncated_words = words[:k]  # Extract the first k words
    truncated_sentence = ' '.join(truncated_words).strip()  # Concatenate and trim spaces
    return truncated_sentence