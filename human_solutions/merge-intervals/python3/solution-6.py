def merge_intervals(intervals):
    if len(intervals) <= 1:
        return intervals
    
    intervals.sort(key=lambda x: x[0])  # Sort intervals based on start
    
    res = [intervals[0]]  # Initialize result with the first interval
    
    for start, end in intervals[1:]:
        if start <= res[-1][1]:  # Check for overlap
            res[-1][1] = max(res[-1][1], end)  # Merge intervals
        else:
            res.append([start, end])  # Add non-overlapping interval
    
    return res

# Test the function
intervals = [[1, 3], [2, 6], [8, 10], [15, 18]]
merged_intervals = merge_intervals(intervals)
print(merged_intervals)