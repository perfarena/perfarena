def min_partition(n):
    max_num = 0
    max_index = 0
    
    for i in range(len(n)):
        current = int(n[i])
        if current > max_num:
            max_num = current
            max_index = i
    
    return max_num

# Test the function
print(min_partition("82734"))  # Output: 8