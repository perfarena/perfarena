def longest_substring_between_equal_chars(s):
    answer = -1
    char_map = {}
    
    for i in range(len(s)):
        if s[i] in char_map:
            answer = max(answer, i - char_map[s[i]] - 1)
        else:
            char_map[s[i]] = i
    
    return answer