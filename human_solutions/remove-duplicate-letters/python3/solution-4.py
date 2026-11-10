from collections import Counter

def removeDuplicateLetters(s):
    counter = Counter(s)
    stack = []
    seen = set()

    for c in s:
        counter[c] -= 1
        if c in seen:
            continue
        while stack and c < stack[-1] and counter[stack[-1]] > 0:
            seen.remove(stack.pop())
        stack.append(c)
        seen.add(c)

    return ''.join(stack)

# Test the function
s = "bcabc"
result = removeDuplicateLetters(s)
print(result)