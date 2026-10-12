def max_in_sliding_window(arr, K):
    result = []
    DQ = []
    
    for i in range(len(arr)):
        # Remove elements out of the window
        if DQ and DQ[0] < i - K + 1:
            DQ.pop(0)
        
        # Remove elements smaller than the current element
        while DQ and arr[DQ[-1]] < arr[i]:
            DQ.pop()
        
        DQ.append(i)
        
        # If window size is reached, store the maximum element
        if i >= K - 1:
            result.append(arr[DQ[0]])
    
    return result

# Example usage
arr = [1, 3, -1, -3, 5, 3, 6, 7]
K = 3
print(max_in_sliding_window(arr, K))