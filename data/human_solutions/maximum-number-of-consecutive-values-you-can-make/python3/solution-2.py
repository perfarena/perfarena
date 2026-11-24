left = {}
right = {}
result = 0

for i, char in enumerate(s):
    if char in left:
        right[char] = i
    else:
        left[char] = i

    for character in left:
        if character in right:
            chars_set = set(s[left[character] + 1:right[character]])
            result += len(chars_set)

return result