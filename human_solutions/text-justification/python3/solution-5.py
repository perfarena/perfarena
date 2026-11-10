current_count = 0
current_words = []

for word in words:
    if len(word) + current_count <= max_width:
        current_words.append(word)
        current_count += len(word) + 1
    else:
        add_sentence(current_count, current_words)
        current_count = len(word) + 1
        current_words = [word]

# Handle remaining words
if current_words:
    add_sentence(current_count, current_words)