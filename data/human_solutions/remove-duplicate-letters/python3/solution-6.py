res = []
seen = set()
for i, char in enumerate(s):
    if char not in seen:
        while res and char < res[-1] and last_pos[res[-1]] > i:
            seen.remove(res.pop())
        seen.add(char)
        res.append(char)
return ''.join(res)