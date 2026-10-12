right = 0
left = 0
max_length = 0
unique = set()

while right < len(s):
    if s[right] not in unique:
        unique.add(s[right])
        max_length = max(max_length, len(unique))
        right += 1
    else:
        unique.remove(s[left])
        left += 1

return max_length