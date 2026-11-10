def fullJustify(words, maxWidth):
    result = []
    n = len(words)
    i = 0

    while i < n:
        j = i + 1
        line_length = len(words[i])
        while j < n and line_length + len(words[j]) + (j - i) <= maxWidth:
            line_length += len(words[j])
            j += 1

        spaces = maxWidth - line_length
        if j - i > 1 and j < n:
            spaces_between_words = spaces // (j - i - 1)
            extra_spaces = spaces % (j - i - 1)
        else:
            spaces_between_words = spaces
            extra_spaces = 0

        row = words[i]
        for k in range(i + 1, j):
            row += ' ' * spaces_between_words
            if extra_spaces > 0:
                row += ' '
                extra_spaces -= 1
            row += words[k]

        row += ' ' * (maxWidth - len(row))
        result.append(row)
        i = j

    return result