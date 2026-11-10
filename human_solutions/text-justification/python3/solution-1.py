def format_text(words, max_width):
    result = []
    line = []
    line_width = 0

    for word in words:
        if line_width + len(word) + len(line) > max_width:
            for i in range(max_width - line_width):
                line[i % (len(line) - 1 or 1)] += ' '
            result.append(''.join(line))
            line = []
            line_width = 0

        line.append(word)
        line_width += len(word)

    last_line = ' '.join(line)
    last_line += ' ' * (max_width - len(last_line))
    result.append(last_line)

    return result

# Example usage
words = ["Hello", "gas", "welcome", "to", "me", "YouTube", "channel,", "so", "today", "we", "are", "going", "to", "solve", "problem"]
max_width = 16
formatted_text = format_text(words, max_width)
for line in formatted_text:
    print(line)