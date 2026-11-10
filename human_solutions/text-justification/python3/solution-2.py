result = []
line = []
line_length = 0
pointer = 0

for word in words:
    if line_length + len(word) + len(line) > max_width:
        result.append(' '.join(line))
        line = []
        line_length = 0

    line.append(word)
    line_length += len(word)

extra_spaces = max_width - line_length
spaces_between_words = extra_spaces // max(1, len(line) - 1)
remainder = extra_spaces % max(1, len(line) - 1)

for j in range(len(line) - 1):
    line[j] += ' ' * spaces_between_words
    if remainder > 0:
        line[j] += ' '
        remainder -= 1

result.append(' '.join(line))

last_line = ' '.join(line)
trailing_space = max_width - len(last_line)
last_line += ' ' * trailing_space

result.append(last_line)

return result