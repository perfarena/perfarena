def partition_labels(s):
    last_index = {char: idx for idx, char in enumerate(s)}
    result = []
    start, end = 0, 0
    
    for i, char in enumerate(s):
        end = max(end, last_index[char])
        if i == end:
            result.append(end - start + 1)
            start = i + 1
    
    return result

# Test the function with a sample input
s = "abacdc"
print(partition_labels(s))  # Output: [3, 3]