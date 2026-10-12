def searchInsertPosition(numbers, target):
    low = 0
    high = len(numbers) - 1
    
    while low <= high:
        mid = (high - low) // 2 + low
        
        if numbers[mid] == target:
            return mid
        elif numbers[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    
    return low

# Test the function
numbers = [1, 3, 5, 6]
target = 5
output = searchInsertPosition(numbers, target)
print(output)  # Output should be 2