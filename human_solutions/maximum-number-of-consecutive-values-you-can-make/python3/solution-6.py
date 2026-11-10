from collections import Counter

def count_palindromes(s):
    left = {}
    result = set()
    
    for i in range(len(s)):
        if s[i] in left:
            result.add((left[s[i]], s[i], i))
        left[s[i]] = i
        
        for character in s:
            if character in left:
                result.add((left[character], character, i))
    
    return len(result)