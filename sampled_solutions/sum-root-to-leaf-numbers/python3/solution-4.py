def dfs(node, num):
    if not node.left and not node.right:
        return int(num)
    
    total = 0
    if node.left:
        total += dfs(node.left, num + str(node.left.val))
    if node.right:
        total += dfs(node.right, num + str(node.right.val))
    
    return total

def sumRootToLeaf(root):
    if not root:
        return 0
    
    return dfs(root, str(root.val))

# Example usage:
# result = sumRootToLeaf(root_node)