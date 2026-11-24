def add_sentence(count, words):
    spaces = max_width - count
    if len(words) == 1 or len(words) == 0:
        sentence = words[0] + ' ' * spaces
    else:
        spaces_between_words = spaces // max(1, len(words) - 1)
        extra_spaces = spaces % max(1, len(words) - 1)
        sentence = ''
        for i, word in enumerate(words):
            sentence += word
            if i < len(words) - 1:
                sentence += ' ' * spaces_between_words
                if extra_spaces > 0:
                    sentence += ' '
                    extra_spaces -= 1
    sentences.append(sentence)