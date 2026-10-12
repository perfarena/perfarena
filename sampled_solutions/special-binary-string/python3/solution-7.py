def partitionLabels(s):
    if not s or len(s) == 0:
        return None
    
    output = []
    last_indices = [-1] * 26
    
    start = end = 0
    for i in range(len(s)):
        end = max(end, last_indices[ord(s[i]) - ord('a')])
        
        if i == end:
            output.append(end - start + 1)
            start = end + 1
    
    return output