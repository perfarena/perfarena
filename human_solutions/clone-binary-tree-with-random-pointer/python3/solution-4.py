index = [0] * 26
result = -1

for i in range(len(s)):
    if index[ord(s[i]) - ord('a')] == 0:
        index[ord(s[i]) - ord('a')] = i + 1
    else:
        result = max(result, i - index[ord(s[i]) - ord('a')])

return result