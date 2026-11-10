def find_longest_repeating_substring(text):
    current_char = ''
    current_count = 0
    max_char = ''
    max_count = 0

    for char in text:
        if char == current_char:
            current_count += 1
        else:
            if current_count > max_count:
                max_char = current_char
                max_count = current_count
            current_char = char
            current_count = 1

    # Check the last repeating substring
    if current_count > max_count:
        max_char = current_char
        max_count = current_count

    longest_substring = max_char * max_count
    return longest_substring

# Test the function
text = "hello Dean hey Granada is a toleration"
result = find_longest_repeating_substring(text)
print(result)  # Output: "ll"