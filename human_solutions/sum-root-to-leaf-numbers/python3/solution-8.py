def DFS(node, current_sum):
    if node is None:
        return 0
    
    current_sum = current_sum * 10 + node.value
    
    if not node.left and not node.right:
        return current_sum
    
    return DFS(node.left, current_sum) + DFS(node.right, current_sum)

# Initialize with root and current sum as 0
total_sum = DFS(root, 0)