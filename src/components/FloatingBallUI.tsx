export default function FloatingBallUI() {
  return (
    <div className="w-full h-full flex items-center justify-center">
      <div className="w-12 h-12 bg-primary-500 rounded-full shadow-lg flex items-center justify-center cursor-pointer hover:bg-primary-600 transition-colors">
        <div className="w-2 h-2 bg-white rounded-full" />
      </div>
    </div>
  )
}
