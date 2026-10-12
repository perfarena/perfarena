def isSubsequence(s, t):
    tracker = 0
    
    for char in t:
        if tracker < len(s) and s[tracker] == char:
            tracker += 1
        
        if tracker == len(s):
            break
    
    return tracker == len(s)