def count_palindromes_v2(s):
    characters = set(s)
    result = 0
    
    for character in characters:
        i = s.index(character)
        j = s.rindex(character)
        
        result += len(set(s[i+1:j]))
    
    return result