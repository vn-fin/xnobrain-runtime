const configuredName = import.meta.env.VITE_XNOBRAIN_APP_NAME?.trim()

export const productName = configuredName || 'XNOBrain'
export const productDescription = import.meta.env.VITE_XNOBRAIN_APP_DESCRIPTION?.trim()
  || 'Install and run the private XNOBrain Docker Web workspace.'
