def find_triplet(arr):
    if len(arr) < 3:
        return False
    
    smallest = float('inf')
    middle = float('inf')
    max_val = float('-inf')
    
    for num in arr:
        if num > middle:
            return True
        elif num > smallest and num < middle:
            middle = num
        elif num < smallest:
            smallest = num
    
    return False

# Input array
arr = [1, 5, 2, 4, 3, 6]
result = find_triplet(arr)
print(result)