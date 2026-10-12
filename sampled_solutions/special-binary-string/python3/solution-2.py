def partition_labels(s):
    last_idx = {char: idx for idx, char in enumerate(s)}
    result = []
    start, end = 0, 0
    
    for i, char in enumerate(s):
        end = max(end, last_idx[char])
        if i == end:
            result.append(end - start + 1)
            start = i + 1
    
    return result

# Test the function with sample input
input_str = "abaabcadef"
output = partition_labels(input_str)
print(output)  # Expected output: [3, 3, 4]