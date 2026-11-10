# Convert the string to a list for easier manipulation
s = list(input_string)
i, j = 0, len(s) - 1

# Two pointers approach to reverse vowels
while i < j:
    if s[i] not in 'aeiouAEIOU':
        i += 1
    elif s[j] not in 'aeiouAEIOU':
        j -= 1
    else:
        s[i], s[j] = s[j], s[i]
        i += 1
        j -= 1

# Convert the list back to a string
result = ''.join(s)
return result