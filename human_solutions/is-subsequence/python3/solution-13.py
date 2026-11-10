def is_subsequence(s, t):
    newStr = ""
    count = 0
    
    for letter in t:
        if count < len(s) and letter == s[count]:
            newStr += letter
            count += 1
    
    return newStr == s

# Test the function
s = "ace"
t = "abcde"
print(is_subsequence(s, t))  # Output should be True