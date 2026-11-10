if end:
    sentence = ' '.join(current_words)
    spaces = max_width - len(sentence)
    if len(current_words) > 1:
        spaces_between_words = spaces // max(1, len(current_words) - 1)
        extra_spaces = spaces % max(1, len(current_words) - 1)
        sentence = ''
        for i, word in enumerate(current_words):
            sentence += word
            if i < len(current_words) - 1:
                sentence += ' ' * spaces_between_words
                if extra_spaces > 0:
                    sentence += ' '
                    extra_spaces -= 1
    sentences.append(sentence)
    return sentences