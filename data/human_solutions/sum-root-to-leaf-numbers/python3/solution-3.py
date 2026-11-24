# Define a helper method for DFS traversal
def dfs(node, sum):
    if not node:
        return 0
    
    sum = sum * 10 + node.val
    
    if not node.left and not node.right:
        total += sum
    
    dfs(node.left, sum)
    dfs(node.right, sum)

# Initialize total sum variable
total = 0

# Call the helper method to start traversal
dfs(root, 0)

# Return the final total sum
return total