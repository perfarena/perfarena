def dailyTemperatures(temperatures):
    result = [0] * len(temperatures)
    stack = []
    
    for i, temp in enumerate(temperatures):
        while stack and temp > stack[-1][0]:
            prev_temp, prev_index = stack.pop()
            result[prev_index] = i - prev_index
        
        stack.append((temp, i))
    
    return result