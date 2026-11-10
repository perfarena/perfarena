int sumRootToLeaf(TreeNode* root, int value = 0) {
    if (root == nullptr) {
        return 0;
    }
    value = value * 10 + root->val;
    if (root->left == nullptr && root->right == nullptr) {
        return value;
    }
    return sumRootToLeaf(root->left, value) + sumRootToLeaf(root->right, value);
}