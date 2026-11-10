def truncate_sentence(s, k):
    words = s.split()  # Split the sentence into words
    truncated_words = words[:k]  # Take the first K words
    truncated_sentence = ' '.join(truncated_words)  # Join the words back into a sentence
    return truncated_sentence