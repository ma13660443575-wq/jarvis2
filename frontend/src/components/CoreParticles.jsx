// CoreParticles — JARVIS「空间 AI 意识体」
// 视觉复刻 jarvis_ai_core_visualizer：
//   60000 绵密发光粒子雾团（核心密、边缘弥散，斐波那契球 + 吸积盘分布）
//   中心线框二十面体核心（wireframe icosahedron）+ 内发光
//   3 层 Torus 空间引力环（随机倾斜、慢速自转）
//   状态动画：idle 呼吸 / listening 纵向波纹 / thinking 向心收缩 / speaking 径向喷射+声波 / executing 高频振荡
//   ACESFilmic 色调映射 + 空间雾 + 鼠标视差 + 柔和 Bloom
// 通过共享 particleState 每帧读取状态与波形。
import { useEffect, useMemo, useRef } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import * as THREE from 'three'
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js'
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js'
import { particleState } from '../lib/particleState'

// ============ 状态配置（参考 visualizer 五态 + confirming/error） ============
const C = (hex) => new THREE.Color(hex)
const STATE_CFG = {
  idle:       { core: C('#00f3ff'), outer: C('#0066ff'), speed: 0.002, pulseFreq: 0.8, spread: 1.0 },
  listening:  { core: C('#10b981'), outer: C('#059669'), speed: 0.005, pulseFreq: 2.5, spread: 1.3 },
  thinking:   { core: C('#a855f7'), outer: C('#6366f1'), speed: 0.02,  pulseFreq: 4.0, spread: 0.7 },
  speaking:   { core: C('#f59e0b'), outer: C('#d97706'), speed: 0.008, pulseFreq: 3.2, spread: 1.5 },
  executing:  { core: C('#ef4444'), outer: C('#b91c1c'), speed: 0.03,  pulseFreq: 5.0, spread: 0.5 },
  confirming: { core: C('#fbbf24'), outer: C('#b45309'), speed: 0.006, pulseFreq: 1.6, spread: 1.1 },
  error:      { core: C('#ff5c5c'), outer: C('#dc2626'), speed: 0.004, pulseFreq: 6.0, spread: 1.2 },
  memory:     { core: C('#8b5cf6'), outer: C('#22d3ee'), speed: 0.012, pulseFreq: 1.4, spread: 2.0 },
}

// 当前插值颜色（模块级，避免重渲染）
const CUR_CORE = new THREE.Color(STATE_CFG.idle.core)
const CUR_OUTER = new THREE.Color(STATE_CFG.idle.outer)

// 高斯软发光纹理
function makeGlowTexture() {
  const c = document.createElement('canvas')
  c.width = c.height = 64
  const ctx = c.getContext('2d')
  const g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32)
  g.addColorStop(0, 'rgba(255,255,255,1)')
  g.addColorStop(0.2, 'rgba(255,255,255,0.8)')
  g.addColorStop(0.6, 'rgba(255,255,255,0.15)')
  g.addColorStop(1, 'rgba(255,255,255,0)')
  ctx.fillStyle = g
  ctx.fillRect(0, 0, 64, 64)
  return new THREE.CanvasTexture(c)
}

// ============ 粒子意识场（60000 发光雾团） ============
function ParticleField() {
  const COUNT = 60000
  const pointsRef = useRef()
  const tex = useMemo(() => makeGlowTexture(), [])

  const { initial, positions, colors } = useMemo(() => {
    const initial = new Float32Array(COUNT * 3)
    const positions = new Float32Array(COUNT * 3)
    const colors = new Float32Array(COUNT * 3)
    const baseColor = STATE_CFG.idle.core
    for (let i = 0; i < COUNT; i++) {
      // 斐波那契球形 + 距离递减分布（核心密、边缘弥散）
      const u = Math.random(), v = Math.random()
      const theta = u * 2 * Math.PI
      const phi = Math.acos(2 * v - 1)
      const radius = 1.2 + Math.pow(Math.random(), 3) * 5.5
      const x = radius * Math.sin(phi) * Math.cos(theta)
      const y = radius * Math.sin(phi) * Math.sin(theta)
      const z = radius * Math.cos(phi)
      initial[i * 3] = x; initial[i * 3 + 1] = y; initial[i * 3 + 2] = z
      positions[i * 3] = x; positions[i * 3 + 1] = y; positions[i * 3 + 2] = z
      colors[i * 3] = baseColor.r; colors[i * 3 + 1] = baseColor.g; colors[i * 3 + 2] = baseColor.b
    }
    return { initial, positions, colors }
  }, [])

  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(positions, 3).setUsage(THREE.DynamicDrawUsage))
    g.setAttribute('color', new THREE.BufferAttribute(colors, 3).setUsage(THREE.DynamicDrawUsage))
    return g
  }, [positions, colors])

  useFrame((st) => {
    const t = st.clock.elapsedTime
    const mode = particleState.mode || 'idle'
    const cfg = STATE_CFG[mode] || STATE_CFG.idle
    CUR_CORE.lerp(cfg.core, 0.04)
    CUR_OUTER.lerp(cfg.outer, 0.04)

    // 整体旋转 + 轻微摆动
    if (pointsRef.current) {
      pointsRef.current.rotation.y = t * cfg.speed
      pointsRef.current.rotation.x = Math.sin(t * 0.2) * 0.1
    }

    const pulse = Math.sin(t * cfg.pulseFreq) * 0.15 + 1.0
    const spread = cfg.spread
    const levels = particleState.levels
    let peak = 0
    if (mode === 'speaking') {
      for (let k = 0; k < levels.length; k++) if (levels[k] > peak) peak = levels[k]
    }

    const p = geometry.attributes.position.array
    const col = geometry.attributes.color.array
    for (let i = 0; i < COUNT; i++) {
      const ix = i * 3, iy = ix + 1, iz = ix + 2
      const initX = initial[ix], initY = initial[iy], initZ = initial[iz]
      let noise = 0
      if (mode === 'listening') {
        // 声浪波动（纵向波纹扩散）
        noise = Math.sin(initY * 3 + t * 8) * 0.25
      } else if (mode === 'thinking') {
        // 向心加速旋转与收缩
        noise = Math.cos(initX * 5 + t * 12) * 0.15
      } else if (mode === 'speaking') {
        // 径向能量喷射脉冲 + 按声音频段震动
        const r = Math.sqrt(initX * initX + initY * initY + initZ * initZ)
        const base = Math.sin(r * 4 - t * 10) * 0.35
        const band = Math.min(63, Math.floor((i / COUNT) * 64))
        const lv = levels[band] || 0
        noise = base * (0.45 + peak * 0.55) + lv * 0.18 * Math.sin(t * 18 + initX * 6)
      } else if (mode === 'executing') {
        // 极高频振荡
        noise = (Math.random() - 0.5) * 0.3
      } else if (mode === 'confirming') {
        noise = Math.sin(t * 1.2 + initZ) * 0.1
      } else if (mode === 'error') {
        noise = Math.abs(Math.sin(t * 6 + initX)) * 0.2
      } else if (mode === 'memory') {
        // 记忆星图：螺旋散开 + 星点脉冲
        const r = Math.sqrt(initX * initX + initY * initY + initZ * initZ)
        noise = Math.sin(r * 2 - t * 3) * 0.28 + Math.cos(initZ * 4 + t * 2) * 0.2
      } else {
        // 自然柔和呼吸
        noise = Math.sin(t * 1.5 + initX) * 0.08
      }
      p[ix] = initX * pulse * spread + noise
      p[iy] = initY * pulse * spread + noise
      p[iz] = initZ * pulse * spread + noise
      col[ix] = CUR_CORE.r; col[iy] = CUR_CORE.g; col[iz] = CUR_CORE.b
    }
    geometry.attributes.position.needsUpdate = true
    geometry.attributes.color.needsUpdate = true
  })

  return (
    <points ref={pointsRef} geometry={geometry}>
      <pointsMaterial
        map={tex}
        size={0.08}
        vertexColors
        transparent
        opacity={0.85}
        blending={THREE.AdditiveBlending}
        depthWrite={false}
      />
    </points>
  )
}

// ============ 中心线框二十面体核心 + 内发光 ============
function WireCore() {
  const meshRef = useRef()
  const glowRef = useRef()
  const tex = useMemo(() => makeGlowTexture(), [])

  useFrame((st) => {
    const t = st.clock.elapsedTime
    const mode = particleState.mode || 'idle'
    const cfg = STATE_CFG[mode] || STATE_CFG.idle
    // 说话时按实时音频强度增强内发光与核心膨胀（Web Audio API 驱动）
    let boost = 0
    if (mode === 'speaking') {
      const lv = particleState.levels
      let peak = 0
      for (let k = 0; k < lv.length; k++) if (lv[k] > peak) peak = lv[k]
      boost = Math.min(1, (particleState.intensity || 0) * 0.6 + peak * 0.5)
    }
    const m = meshRef.current
    if (m) {
      m.rotation.y = -t * cfg.speed * 2
      m.rotation.z = t * cfg.speed
      m.material.color.copy(CUR_CORE)
      m.material.opacity = mode === 'speaking' ? 0.45 + boost * 0.4 : 0.45
      m.scale.setScalar((Math.sin(t * cfg.pulseFreq) * 0.06 + 1.0) + boost * 0.22)
    }
    if (glowRef.current) {
      glowRef.current.material.color.copy(CUR_CORE)
      const base = 0.35 + Math.sin(t * cfg.pulseFreq) * 0.12
      glowRef.current.material.opacity = Math.min(1, base + boost * 0.55)
      const s = 1.5 + boost * 1.4
      glowRef.current.scale.set(s, s, 1)
    }
  })

  return (
    <group>
      {/* 线框二十面体核心 */}
      <mesh ref={meshRef}>
        <icosahedronGeometry args={[0.8, 4]} />
        <meshBasicMaterial wireframe transparent opacity={0.45} blending={THREE.AdditiveBlending} depthWrite={false} />
      </mesh>
      {/* 内发光 */}
      <sprite ref={glowRef} scale={[1.5, 1.5, 1]}>
        <spriteMaterial map={tex} color="#00f3ff" transparent opacity={0.4} depthWrite={false} blending={THREE.AdditiveBlending} />
      </sprite>
    </group>
  )
}

// ============ 3 层空间引力环 ============
function GravityRings() {
  const groupRef = useRef()
  const rings = useMemo(() => {
    const list = []
    for (let i = 0; i < 3; i++) {
      list.push({
        geo: new THREE.TorusGeometry(3.2 + i * 1.2, 0.012, 16, 160),
        rx: Math.PI * Math.random(),
        ry: Math.PI * Math.random(),
      })
    }
    return list
  }, [])

  useFrame((st) => {
    const t = st.clock.elapsedTime
    const mode = particleState.mode || 'idle'
    const cfg = STATE_CFG[mode] || STATE_CFG.idle
    const grp = groupRef.current
    if (!grp) return
    grp.children.forEach((ring, index) => {
      ring.rotation.x += 0.001 * (index + 1)
      ring.rotation.y += 0.002 * (index + 1)
      ring.material.color.copy(CUR_OUTER)
      ring.material.opacity = 0.32 + Math.sin(t * 1.2 + index) * 0.08
    })
  })

  return (
    <group ref={groupRef}>
      {rings.map((r, i) => (
        <mesh key={i} geometry={r.geo} rotation={[r.rx, r.ry, 0]}>
          <meshBasicMaterial transparent opacity={0.35} blending={THREE.AdditiveBlending} depthWrite={false} />
        </mesh>
      ))}
    </group>
  )
}

// ============ 鼠标视差 ============
function CamRig() {
  const { camera } = useThree()
  const mouse = useRef({ x: 0, y: 0 })

  useEffect(() => {
    const onMove = (e) => {
      mouse.current.x = (e.clientX / window.innerWidth) * 2 - 1
      mouse.current.y = -(e.clientY / window.innerHeight) * 2 + 1
    }
    window.addEventListener('mousemove', onMove)
    return () => window.removeEventListener('mousemove', onMove)
  }, [])

  useFrame(() => {
    camera.position.x += (mouse.current.x * 1.5 - camera.position.x) * 0.05
    camera.position.y += (mouse.current.y * 1.5 - camera.position.y) * 0.05
    camera.lookAt(0, 0, 0)
  })
  return null
}

// ============ 柔和 Bloom 后处理（priority>0 接管渲染） ============
function Bloom() {
  const { gl, scene, camera, size } = useThree()
  const composer = useRef()

  useEffect(() => {
    const c = new EffectComposer(gl)
    c.setPixelRatio(gl.getPixelRatio())
    c.setSize(size.width, size.height)
    c.addPass(new RenderPass(scene, camera))
    const bloom = new UnrealBloomPass(
      new THREE.Vector2(size.width * gl.getPixelRatio(), size.height * gl.getPixelRatio()),
      0.4, 0.6, 0.7)
    c.addPass(bloom)
    c.addPass(new OutputPass())
    composer.current = c
    return () => { c.dispose() }
  }, [gl, scene, camera, size])

  useFrame(() => { if (composer.current) composer.current.render() }, 1)
  return null
}

export default function CoreParticles() {
  return (
    <Canvas
      camera={{ position: [0, 0, 14], fov: 55, near: 0.1, far: 200 }}
      gl={{
        antialias: true,
        alpha: true,
        powerPreference: 'high-performance',
        toneMapping: THREE.ACESFilmicToneMapping,
        toneMappingExposure: 1.25,
      }}
      dpr={[1, 2]}
      style={{ background: 'transparent' }}
    >
      <fogExp2 attach="fog" args={['#000000', 0.018]} />
      <ParticleField />
      <WireCore />
      <GravityRings />
      <CamRig />
      <Bloom />
    </Canvas>
  )
}
