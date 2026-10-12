def partition_labels(s):
    last_occurrence = {char: idx for idx, char in enumerate(s)}
    partitions = []
    start = end = 0
    
    for idx, char in enumerate(s):
        end = max(end, last_occurrence[char])
        if idx == end:
            partitions.append(end - start + 1)
            start = idx + 1
    
    return partitions

# Test the function with the input string
input_string = "abacdcde"
result = partition_labels(input_string)
print(result)