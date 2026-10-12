def compute_intersection(arr1, arr2):
    output = []
    for num in arr1:
        if num in arr2:
            output.append(num)
    
    # Remove duplicates
    output = list(set(output))
    
    return output